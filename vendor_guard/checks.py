"""Risk rules for a vendor request.

Each rule looks at the request, what the documents say, what the master data
says and returns findings. The decision at the end is simple on purpose:

  blocker anywhere      -> rejected, the requester gets the reasons
  bank change           -> always needs an approver (four eyes)
  any error or warning  -> needs an approver
  high risk signal      -> needs two different approvers
  nothing found         -> approved without a person (new vendors only)
"""
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from rapidfuzz import fuzz

SEVERITY_ORDER = ["blocker", "error", "warning", "info"]
HIGH_RISK = {"V06", "V08", "V10"}


@dataclass
class Risk:
    id: str
    severity: str
    title: str
    detail: str

    def as_dict(self):
        return asdict(self)


def clean(v):
    return re.sub(r"\s+", "", str(v or "")).upper()


def iban_ok(iban):
    s = clean(iban)
    if len(s) < 15 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    try:
        digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    except ValueError:
        return False
    return int(digits) % 97 == 1


def digits(v):
    return re.sub(r"\D", "", str(v or ""))


def domain(email):
    return str(email or "").rsplit("@", 1)[-1].lower().strip()


def norm_company(name):
    n = str(name or "").lower()
    for k, v in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        n = n.replace(k, v)
    n = re.sub(r"\b(gmbh|ag|kg|ug|ohg|e\.?k\.?|co\.?|und|&|haftungsbeschraenkt|b\.?v\.?)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def next_payment_run(today: date, weekdays: list[int]) -> date:
    for i in range(0, 8):
        d = today + timedelta(days=i)
        if d.weekday() in weekdays:
            return d
    return today


@dataclass
class Case:
    """Everything the rules look at. vendor is the merged request (form plus document)."""
    req: dict
    extracted: dict
    partner: dict | None
    current: dict | None
    sanctions_hits: list
    vies_status: str | None
    today: date
    settings: object

    @property
    def vendor(self):
        return self.req["vendor"]

    @property
    def bank_change(self):
        return self.req["type"] == "bank_change"


def field_checks(c):
    """V01: what stammdaten-check found wrong with the fields."""
    if c.partner is None:
        yield Risk("V01", "error", "Master data check not reachable",
                   "stammdaten-check did not answer after three tries. Check the fields by hand.")
    else:
        for p in c.partner["problems"]:
            if p["code"] in ("IBAN_IN_USE", "IBAN_COUNTRY_DIFFERS"):
                continue  # covered by V03 and V05
            yield Risk("V01", p["severity"], f"Field check: {p['field']}", p["message"])
    if not iban_ok(c.vendor.get("iban")):
        problems = c.partner["problems"] if c.partner else []
        if not any(p["field"] == "iban" and p["severity"] == "blocker" for p in problems):
            yield Risk("V01", "blocker", "Field check: iban", "IBAN missing or check digits wrong")


def duplicates(c):
    """V02: a new vendor that is already in the master."""
    if c.partner and not c.bank_change:
        for d in c.partner["duplicates"]:
            if d["score"] >= 90:
                yield Risk("V02", "error", "Vendor may already exist",
                           f"{d['object']} {d['id']} {d['name']} (score {d['score']:.0f}: {', '.join(d['reasons'])})")


def iban_in_use(c):
    """V03: the account belongs to another partner already."""
    if c.partner:
        others = [o for o in c.partner.get("ibanOwners", []) if o["id"] != c.req.get("existingId")]
        if others:
            yield Risk("V03", "error", "IBAN already used by another partner",
                       ", ".join(f"{o['object']} {o['id']} {o['name']}" for o in others))


def bank_change(c):
    """V04: every bank change gets a second person."""
    if not c.bank_change:
        return
    old = ", ".join(b["iban"] for b in (c.current or {}).get("banks", []) if b.get("iban")) or "none on file"
    yield Risk("V04", "warning", "Bank change on existing vendor",
               f"on file: {old}, requested: {clean(c.vendor.get('iban'))}")
    if c.current and c.current.get("blocked"):
        yield Risk("V04", "error", "Vendor is blocked or flagged for deletion", "Clarify why before changing bank data.")


def foreign_bank(c):
    """V05: bank country differs from vendor country."""
    cc, iban = clean(c.vendor.get("country")), clean(c.vendor.get("iban"))
    if cc and iban_ok(iban) and iban[:2] != cc:
        yield Risk("V05", "warning", "Bank in another country", f"vendor in {cc}, account in {iban[:2]}")


def unknown_sender(c):
    """V06: the request came from an address the vendor does not use."""
    if not (c.bank_change and c.current):
        return
    on_file, sender = domain(c.current.get("email")), domain(c.req.get("sourceEmail"))
    if not sender:
        yield Risk("V06", "error", "Sender of the change request unknown",
                   "Record the email address the request came from.")
    elif on_file and sender != on_file:
        yield Risk("V06", "error", "Request from an unknown email domain",
                   f"request came from {sender}, vendor uses {on_file}")


def account_holder(c):
    """V07: holder name differs from vendor name. The bank checks this again with
    Verification of Payee when paying, this is the early warning."""
    holder, name = c.vendor.get("accountHolder"), c.vendor.get("name")
    if holder and name:
        sim = fuzz.token_set_ratio(norm_company(holder), norm_company(name))
        if sim < 80:
            yield Risk("V07", "warning", "Account holder differs from vendor name",
                       f"holder '{holder}' vs vendor '{name}' (similarity {sim:.0f})")


def payment_run(c):
    """V08: a change right before a payment run leaves no time to check."""
    if not c.bank_change:
        return
    run = next_payment_run(c.today, c.settings.payment_run_weekdays)
    days = (run - c.today).days
    if days <= c.settings.payment_run_warning_days:
        yield Risk("V08", "error", "Bank change right before a payment run",
                   f"next payment run {run.isoformat()}, {days} day(s) away. Hold the vendor in the run until approved.")


def document_vs_form(c):
    """V09: what the PDF says against what was typed in."""
    doc, v = c.extracted.get("fields", {}), c.vendor
    if doc.get("iban") and v.get("iban") and clean(doc["iban"]) != clean(v["iban"]):
        yield Risk("V09", "error", "IBAN in document differs from the form",
                   f"document {doc['iban']}, form {clean(v['iban'])}")
    if doc.get("vatId") and v.get("vatId") and clean(doc["vatId"]) != clean(v["vatId"]):
        yield Risk("V09", "error", "VAT ID in document differs from the form",
                   f"document {doc['vatId']}, form {v['vatId']}")
    if c.bank_change and c.current and doc.get("phoneInDocument"):
        if digits(doc["phoneInDocument"])[-7:] != digits(c.current.get("phone"))[-7:]:
            yield Risk("V09", "info", "Letter shows a different phone number",
                       f"letter: {doc['phoneInDocument']}, on file: {c.current.get('phone')}. Call the number on file.")


def sanctions(c):
    """V10: a fuzzy name hit is a reason to look, never a block on its own."""
    for hit in c.sanctions_hits:
        yield Risk("V10", "error", "Possible sanctions list match",
                   f"{hit['name']} (score {hit['score']:.0f}, {hit['source']}). Compliance must clear this.")


def vies(c):
    """V11: VAT ID at the EU VIES service, if switched on."""
    if c.vies_status == "invalid":
        yield Risk("V11", "error", "VAT ID not valid according to VIES", str(c.vendor.get("vatId")))
    elif c.vies_status == "unavailable":
        yield Risk("V11", "info", "VIES not reachable", "VAT ID not confirmed online, check later.")


def pressure(c):
    """V12: urgency and secrecy, typical for payment diversion letters."""
    words = c.extracted.get("pressureWords", [])
    if words:
        yield Risk("V12", "warning", "Pressure language in the letter", ", ".join(words))


RULES = [field_checks, duplicates, iban_in_use, bank_change, foreign_bank, unknown_sender,
         account_holder, payment_run, document_vs_form, sanctions, vies, pressure]


def evaluate(case: Case) -> list[Risk]:
    return [risk for rule in RULES for risk in rule(case)]


def decide(req_type: str, risks: list[Risk]) -> tuple[str, int]:
    """Return (status, approvals needed)."""
    sev = {r.severity for r in risks}
    if "blocker" in sev:
        return "rejected", 0
    if any(r.id in HIGH_RISK for r in risks):
        return "pending_approval", 2
    if req_type == "bank_change" or sev & {"error", "warning"}:
        return "pending_approval", 1
    return "approved", 0
