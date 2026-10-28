"""The two things this service does: take in a request, and record a decision."""
import csv
from datetime import date

from . import checks, extract
from .clients import Stammdaten, resume_n8n, vies_check
from .sanctions import Screening
from .security import TokenError, make_token, read_token
from .store import Store, ref

VENDOR_FIELDS = ("name", "name2", "street", "postalCode", "city", "country", "vatId", "iban",
                 "accountHolder", "email", "phone")


class BadRequest(Exception):
    pass


class Service:
    def __init__(self, settings, store: Store | None = None, stammdaten: Stammdaten | None = None,
                 screening: Screening | None = None, today=None):
        self.s = settings
        self.store = store or Store(settings.home, settings.audit_key)
        self.stammdaten = stammdaten or Stammdaten(settings.stammdaten_url, settings.stammdaten_key)
        self.screening = screening or Screening(settings.sanctions_file)
        self.today = today or date.today

    # ---------- intake ----------

    def merge(self, req, extracted, current):
        """Form values win over the document, the document fills gaps. For a bank
        change, name and address come from the master record, not from the letter."""
        form = {k: (req["vendor"].get(k) or "").strip() for k in VENDOR_FIELDS}
        doc = extracted.fields
        merged = dict(form)
        for k in ("iban", "vatId", "accountHolder"):
            if not merged[k] and doc.get(k):
                merged[k] = doc[k]
        if current:
            for k in ("name", "name2", "street", "postalCode", "city", "country", "vatId"):
                merged[k] = merged[k] or current.get(k, "")
        merged["iban"] = checks.clean(merged["iban"])
        merged["country"] = merged["country"].upper()
        return merged

    def intake(self, req: dict, idempotency_key=None, actor="api"):
        if idempotency_key and (existing := self.store.case_for_key(idempotency_key)):
            return self.store.get_case(existing), False, []

        extracted = self._read_documents(req)
        current = self._current_vendor(req)
        vendor = self.merge(req, extracted, current)
        merged = {k: v for k, v in req.items() if k != "documents"} | {"vendor": vendor}
        partner = self.stammdaten.partner_check(vendor, req.get("existingId"))
        vies = vies_check(vendor["vatId"]) if self.s.vies and vendor.get("vatId") else None

        risks = checks.evaluate(checks.Case(merged, extracted.as_dict(), partner, current,
                                             self._screen(vendor), vies, self.today(), self.s))
        status, needed = checks.decide(req["type"], risks)
        case = {
            "type": req["type"], "status": status, "approvals_needed": needed,
            "requester_name": req["requester"].get("name", ""),
            "requester_email": req["requester"]["email"].lower(),
            "source_email": (req.get("sourceEmail") or "").lower(),
            "existing_id": req.get("existingId"),
            "request": merged, "extracted": extracted.as_dict(), "partner": partner, "current": current,
            "risks": [r.as_dict() for r in risks],
        }
        case_id, created = self.store.create_case(idempotency_key, case, actor)
        if not created:
            return self.store.get_case(case_id), False, []

        links = []
        if status == "approved":
            self._export(case_id)
        elif status == "pending_approval":
            links = self._issue_links(case_id, case["requester_email"])
        return self.store.get_case(case_id), True, links

    @staticmethod
    def _read_documents(req):
        try:
            return extract.from_documents(req.get("documents") or [])
        except (ValueError, TypeError) as e:
            raise BadRequest(f"document could not be read: {e}") from None

    def _current_vendor(self, req):
        """The vendor as it is on file. None for new vendors, and when stammdaten-check
        is down (V01 then tells the approver to check by hand)."""
        if req["type"] != "bank_change":
            return None
        if not req.get("existingId"):
            raise BadRequest("a bank change needs existingId, the vendor number")
        current, reachable = self.stammdaten.vendor(req["existingId"])
        if reachable and current is None:
            raise BadRequest(f"vendor {req['existingId']} not found in the master data")
        return current

    def _screen(self, vendor):
        hits = self.screening.check(vendor["name"])
        holder = vendor.get("accountHolder")
        if holder and checks.norm_company(holder) != checks.norm_company(vendor["name"]):
            hits += self.screening.check(holder)
        return hits

    def _issue_links(self, case_id, requester):
        links = []
        for approver in self.s.approvers:
            if approver == requester:
                continue  # nobody approves their own request
            token, nonce, exp = make_token(self.s.link_secret, case_id, approver, self.s.link_hours)
            self.store.add_link(nonce, case_id, approver, exp)
            links.append({"approver": approver, "url": f"{self.s.public_url}/approvals/{token}"})
        return links

    # ---------- decisions ----------

    def open_link(self, token):
        data = read_token(self.s.link_secret, token)
        link = self.store.link(data["n"])
        if link is None or link["case_id"] != data["c"] or link["approver"] != data["a"]:
            raise TokenError("link is not valid")
        case = self.store.get_case(data["c"])
        return data, link, case

    def decide(self, token, decision, callback_phone, comment):
        data, link, case = self.open_link(token)
        approver = data["a"]
        self._check_decision(case, link, approver, decision, comment)
        if decision == "approve" and case["type"] == "bank_change":
            self._check_callback(case, callback_phone)

        new_status = self._status_after(case, decision)
        export_file = self._export_name(case["id"]) if new_status == "approved" else None
        try:
            self.store.record_approval(case["id"], data["n"], approver, decision, callback_phone, comment,
                                       new_status, export_file)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        if new_status == "approved":
            self._write_export(self.store.get_case(case["id"]), export_file)
        if new_status != "pending_approval":
            self._resume(case["id"])
        return self.store.get_case(case["id"])

    @staticmethod
    def _check_decision(case, link, approver, decision, comment):
        if link["used_at"]:
            raise BadRequest("this link was already used")
        if case["status"] != "pending_approval":
            raise BadRequest(f"case is already {case['status']}")
        if approver == case["requester_email"]:
            raise BadRequest("you cannot approve your own request")
        if decision not in ("approve", "reject"):
            raise BadRequest("decision must be approve or reject")
        if decision == "reject" and not (comment or "").strip():
            raise BadRequest("say why you reject it, the requester gets this text")

    @staticmethod
    def _check_callback(case, callback_phone):
        """The approver must have called the number on file, not the one in the letter."""
        on_file = checks.digits((case["current"] or {}).get("phone"))
        called = checks.digits(callback_phone)
        if not on_file:
            raise BadRequest("no phone number on file, a bank change cannot be confirmed by call back")
        if len(called) < 7 or called[-7:] != on_file[-7:]:
            raise BadRequest("enter the number you called. It must be the number on file, not the one in the letter")

    @staticmethod
    def _status_after(case, decision):
        if decision == "reject":
            return "rejected"
        approved = sum(a["decision"] == "approve" for a in case["approvals"])
        return "approved" if approved + 1 >= case["approvals_needed"] else "pending_approval"

    def _resume(self, case_id):
        case = self.store.get_case(case_id)
        payload = {"caseId": case_id, "ref": case["ref"], "status": case["status"],
                   "decisions": case["approvals"], "exportFile": case["export_file"]}
        if not case["resume_url"]:
            self.store.note(case_id, "system", "no_workflow_waiting", {})
            return
        ok = resume_n8n(case["resume_url"], payload)
        self.store.note(case_id, "system", "n8n_resumed" if ok else "n8n_resume_failed",
                        {"url": case["resume_url"]})

    # ---------- export ----------

    def _export_name(self, case_id):
        return f"{ref(case_id)}.csv"

    def _export(self, case_id):
        name = self._export_name(case_id)
        with self.store.connect() as con:
            con.execute("UPDATE cases SET export_file = ?, decided_at = created_at WHERE id = ?", (name, case_id))
        self._write_export(self.store.get_case(case_id), name)

    def _write_export(self, case, name):
        """One CSV per case, ready for upload. New vendors use the Business Partner
        staging layout of stammdaten-check, bank changes a before/after record."""
        row = onboarding_row(case) if case["type"] == "onboarding" else bank_change_row(case)
        with open(self.store.home / "exports" / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)
        self.store.note(case["id"], "system", "export_written", {"file": name})


def onboarding_row(case):
    v = case["request"]["vendor"]
    return {"LEGACY_KEY": "", "BU_GROUP": "BP04", "BU_TYPE": "2", "TITLE": "0003",
            "NAME_ORG1": v["name"], "NAME_ORG2": v["name2"], "STREET": v["street"], "HOUSE_NUM1": "",
            "POST_CODE1": v["postalCode"], "CITY1": v["city"], "COUNTRY": v["country"],
            "LANGU": "D" if v["country"] in ("DE", "AT", "CH") else "E",
            "TAXTYPE": f"{v['country']}0" if v["vatId"] else "", "TAXNUM": v["vatId"],
            "SMTP_ADDR": v["email"], "IBAN": v["iban"], "CASE": case["ref"]}


def bank_change_row(case):
    v = case["request"]["vendor"]
    approvals = [a for a in case["approvals"] if a["decision"] == "approve"]
    return {"LIFNR": case["existing_id"], "NAME1": v["name"],
            "IBAN_OLD": ", ".join(b["iban"] for b in (case["current"] or {}).get("banks", []) if b.get("iban")),
            "IBAN_NEW": v["iban"], "KOINH": v["accountHolder"] or v["name"],
            "APPROVED_BY": "; ".join(a["approver"] for a in approvals),
            "CALLBACK": "; ".join(a["callback_phone"] or "" for a in approvals), "CASE": case["ref"]}
