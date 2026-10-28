"""SQLite storage: cases, approvals, one-time links, idempotency keys, audit log."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .security import chain_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id               INTEGER PRIMARY KEY,
    created_at       TEXT NOT NULL,
    type             TEXT NOT NULL,               -- onboarding or bank_change
    status           TEXT NOT NULL,               -- approved, rejected, pending_approval
    approvals_needed INTEGER NOT NULL DEFAULT 0,
    requester_name   TEXT,
    requester_email  TEXT NOT NULL,
    source_email     TEXT,
    existing_id      TEXT,
    request_json     TEXT NOT NULL,
    extracted_json   TEXT NOT NULL,
    partner_json     TEXT,
    current_json     TEXT,
    risks_json       TEXT NOT NULL,
    resume_url       TEXT,
    decided_at       TEXT,
    export_file      TEXT
);
CREATE TABLE IF NOT EXISTS idempotency (
    key      TEXT PRIMARY KEY,
    case_id  INTEGER NOT NULL REFERENCES cases(id)
);
CREATE TABLE IF NOT EXISTS links (
    nonce      TEXT PRIMARY KEY,
    case_id    INTEGER NOT NULL REFERENCES cases(id),
    approver   TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id             INTEGER PRIMARY KEY,
    case_id        INTEGER NOT NULL REFERENCES cases(id),
    at             TEXT NOT NULL,
    approver       TEXT NOT NULL,
    decision       TEXT NOT NULL,                 -- approve or reject
    callback_phone TEXT,
    comment        TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,
    case_id INTEGER,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    data    TEXT NOT NULL,
    prev    TEXT NOT NULL,
    hash    TEXT NOT NULL
);
"""

JSON_COLS = ("request", "extracted", "partner", "current", "risks")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ref(case_id):
    return f"VG-{case_id:05d}"


class Store:
    def __init__(self, home, audit_key):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "exports").mkdir(exist_ok=True)
        self.path = self.home / "vendor-guard.db"
        self.audit_key = audit_key
        with self.connect() as con:
            con.executescript(SCHEMA)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    # ---------- audit ----------

    def log(self, con, case_id, actor, action, data):
        last = con.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else "0" * 64
        at = now()
        entry = {"at": at, "case": case_id, "actor": actor, "action": action, "data": data}
        h = chain_hash(self.audit_key, prev, entry)
        con.execute("INSERT INTO audit (at, case_id, actor, action, data, prev, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (at, case_id, actor, action, json.dumps(data, ensure_ascii=False, sort_keys=True), prev, h))

    def verify_audit(self):
        """Recompute the chain. Returns (ok, entries checked, first broken id)."""
        prev = "0" * 64
        with self.connect() as con:
            rows = con.execute("SELECT * FROM audit ORDER BY id").fetchall()
        for r in rows:
            entry = {"at": r["at"], "case": r["case_id"], "actor": r["actor"],
                     "action": r["action"], "data": json.loads(r["data"])}
            if r["prev"] != prev or chain_hash(self.audit_key, prev, entry) != r["hash"]:
                return False, len(rows), r["id"]
            prev = r["hash"]
        return True, len(rows), None

    def audit_head(self):
        with self.connect() as con:
            r = con.execute("SELECT id, at, hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else {"id": 0, "at": None, "hash": "0" * 64}

    def audit_for(self, case_id):
        with self.connect() as con:
            rows = con.execute("SELECT id, at, actor, action, data FROM audit WHERE case_id = ? ORDER BY id",
                               (case_id,)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # ---------- cases ----------

    def case_for_key(self, key):
        with self.connect() as con:
            r = con.execute("SELECT case_id FROM idempotency WHERE key = ?", (key,)).fetchone()
        return r["case_id"] if r else None

    def create_case(self, key, c, actor):
        with self.connect() as con:
            if key:
                # check again inside the transaction, a retried webhook can arrive twice at once
                r = con.execute("SELECT case_id FROM idempotency WHERE key = ?", (key,)).fetchone()
                if r:
                    return r["case_id"], False
            cur = con.execute(
                "INSERT INTO cases (created_at, type, status, approvals_needed, requester_name, requester_email, "
                "source_email, existing_id, request_json, extracted_json, partner_json, current_json, risks_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), c["type"], c["status"], c["approvals_needed"], c["requester_name"], c["requester_email"],
                 c["source_email"], c["existing_id"], json.dumps(c["request"]), json.dumps(c["extracted"]),
                 json.dumps(c["partner"]), json.dumps(c["current"]), json.dumps(c["risks"])))
            case_id = cur.lastrowid
            if key:
                con.execute("INSERT INTO idempotency (key, case_id) VALUES (?, ?)", (key, case_id))
            self.log(con, case_id, actor, "case_created",
                     {"type": c["type"], "status": c["status"], "approvalsNeeded": c["approvals_needed"],
                      "risks": [r["id"] for r in c["risks"]], "iban": c["request"]["vendor"].get("iban")})
        return case_id, True

    def get_case(self, case_id):
        with self.connect() as con:
            r = con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
            if r is None:
                return None
            approvals = con.execute("SELECT at, approver, decision, callback_phone, comment FROM approvals "
                                    "WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
        c = dict(r)
        for col in JSON_COLS:
            c[col] = json.loads(c.pop(f"{col}_json") or "null")
        c["ref"] = ref(case_id)
        c["approvals"] = [dict(a) for a in approvals]
        return c

    def list_cases(self, limit=200):
        with self.connect() as con:
            rows = con.execute("SELECT id, created_at, type, status, requester_email, existing_id, request_json, "
                               "risks_json FROM cases ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            req = json.loads(r["request_json"])
            out.append({"id": r["id"], "ref": ref(r["id"]), "createdAt": r["created_at"], "type": r["type"],
                        "status": r["status"], "requester": r["requester_email"], "existingId": r["existing_id"],
                        "vendor": req["vendor"].get("name"), "risks": len(json.loads(r["risks_json"]))})
        return out

    def set_resume_url(self, case_id, url):
        with self.connect() as con:
            con.execute("UPDATE cases SET resume_url = ? WHERE id = ?", (url, case_id))
            self.log(con, case_id, "n8n", "resume_url_registered", {"url": url})

    # ---------- links and approvals ----------

    def add_link(self, nonce, case_id, approver, expires_at):
        with self.connect() as con:
            con.execute("INSERT INTO links (nonce, case_id, approver, expires_at) VALUES (?, ?, ?, ?)",
                        (nonce, case_id, approver, expires_at))
            self.log(con, case_id, "system", "approval_link_issued", {"approver": approver})

    def link(self, nonce):
        with self.connect() as con:
            r = con.execute("SELECT * FROM links WHERE nonce = ?", (nonce,)).fetchone()
        return dict(r) if r else None

    def record_approval(self, case_id, nonce, approver, decision, callback_phone, comment, new_status, export_file):
        """One transaction: use the link, store the decision, update the case, write the audit entry."""
        with self.connect() as con:
            used = con.execute("UPDATE links SET used_at = ? WHERE nonce = ? AND used_at IS NULL",
                               (now(), nonce)).rowcount
            if used != 1:
                raise ValueError("this link was already used")
            con.execute("INSERT INTO approvals (case_id, at, approver, decision, callback_phone, comment) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (case_id, now(), approver, decision, callback_phone, comment))
            if new_status != "pending_approval":
                con.execute("UPDATE cases SET status = ?, decided_at = ?, export_file = ? WHERE id = ?",
                            (new_status, now(), export_file, case_id))
            self.log(con, case_id, approver, f"decision_{decision}",
                     {"callbackPhone": callback_phone, "comment": comment, "statusAfter": new_status})

    def note(self, case_id, actor, action, data):
        with self.connect() as con:
            self.log(con, case_id, actor, action, data)
