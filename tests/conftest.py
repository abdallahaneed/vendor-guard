from datetime import date
from pathlib import Path

import pytest

from vendor_guard import samples
from vendor_guard.config import Settings
from vendor_guard.sanctions import Screening
from vendor_guard.service import Service

FIXTURES = Path(__file__).parent / "fixtures"

VENDOR = {
    "object": "vendor", "id": "300001", "name": "Caspar & Walter GmbH", "name2": "",
    "street": "Kochallee 154", "postalCode": "50752", "city": "Niesky", "country": "DE",
    "vatId": "DE617122350", "phone": "+49 228 4171864", "email": "buchhaltung@caspar-001.example.de",
    "blocked": False,
    "banks": [{"country": "DE", "bankKey": "37040044", "account": "532013000",
               "iban": "DE89370400440532013000", "holder": "Caspar & Walter GmbH"}],
}


class FakeStammdaten:
    """Stands in for stammdaten-check. Records calls, answers like the real API."""

    def __init__(self):
        self.vendors = {"300001": VENDOR}
        self.duplicates = []
        self.iban_owners = {}
        self.down = False

    def partner_check(self, vendor, existing_id=None):
        if self.down:
            return None
        owners = [o for o in self.iban_owners.get(vendor.get("iban"), []) if o["id"] != existing_id]
        problems = []
        if owners:
            problems.append({"code": "IBAN_IN_USE", "field": "iban", "severity": "error", "message": "in use"})
        return {"runId": 1, "ok": not problems, "worstSeverity": None, "problems": problems,
                "duplicates": self.duplicates, "ibanOwners": owners}

    def vendor(self, lifnr):
        if self.down:
            return None, False
        return self.vendors.get(lifnr), True


def pdf_doc(lines, name="doc.pdf"):
    return samples._doc(name, samples._pdf(lines, "test"))


def iban(country, bban):
    return samples._iban(country, bban)


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.home = str(tmp_path / "home")
    s.public_url = "http://test"
    s.link_secret = "test-link-secret"
    s.audit_key = "test-audit-key"
    s.approvers = ["anna@example.com", "ben@example.com", "lena.brandt@example.com"]
    s.payment_run_weekdays = [1, 4]
    s.vies = False
    return s


@pytest.fixture
def resumed(monkeypatch):
    calls = []

    def fake(url, payload, tries=3, backoff=1.0):
        calls.append((url, payload))
        return True
    monkeypatch.setattr("vendor_guard.service.resume_n8n", fake)
    return calls


@pytest.fixture
def svc(settings, resumed):
    # a Saturday, next payment run Tuesday: three days away, so V08 stays quiet
    return Service(settings, stammdaten=FakeStammdaten(),
                   screening=Screening(FIXTURES / "sanctions-sample.xml"),
                   today=lambda: date(2026, 11, 7))


@pytest.fixture
def requester():
    return {"name": "Lena Brandt", "email": "lena.brandt@example.com"}
