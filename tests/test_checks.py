from datetime import date

from conftest import VENDOR, iban, pdf_doc

from vendor_guard import checks
from vendor_guard.sanctions import Screening
from conftest import FIXTURES


def ids(case):
    return {r["id"] for r in case["risks"]}


def bank_change(requester, **over):
    new = over.pop("iban", iban("DE", "500105170648489890"))
    body = {"type": "bank_change", "requester": requester, "existingId": "300001",
            "sourceEmail": VENDOR["email"], "vendor": {"iban": new, "accountHolder": VENDOR["name"]},
            "documents": [pdf_doc([f"IBAN: {new}", f"Kontoinhaber: {VENDOR['name']}",
                                   f"Telefon: {VENDOR['phone']}"])]}
    body.update(over)
    return body


def test_genuine_bank_change_needs_one_approver(svc, requester):
    case, created, links = svc.intake(bank_change(requester))
    assert created
    assert case["status"] == "pending_approval"
    assert case["approvals_needed"] == 1
    assert ids(case) == {"V04"}
    # the requester is on the approver list but gets no link
    assert {l["approver"] for l in links} == {"anna@example.com", "ben@example.com"}


def test_fraud_pattern(svc, requester):
    fraud = iban("NL", "INGB0001234567")
    body = bank_change(requester, iban=fraud, sourceEmail="buchhaltung@caspar-finance.example.de",
                       vendor={"iban": fraud},
                       documents=[pdf_doc(["DRINGEND: neue Bankverbindung ab sofort", f"IBAN: {fraud}",
                                           "Kontoinhaber: M. de Vries Trading", "Telefon: +49 1577 3920114",
                                           "Bitte vertraulich behandeln."])])
    case, _, _ = svc.intake(body)
    found = ids(case)
    assert {"V04", "V05", "V06", "V07", "V09", "V10", "V12"} <= found
    assert case["approvals_needed"] == 2   # V06 and V10 are high risk
    sanction = next(r for r in case["risks"] if r["id"] == "V10")
    assert "de Vries" in sanction["detail"]


def test_payment_run_close(settings, requester, resumed):
    from vendor_guard.service import Service
    from conftest import FakeStammdaten
    wednesday = Service(settings, stammdaten=FakeStammdaten(), screening=Screening(None),
                        today=lambda: date(2026, 11, 4))
    case, _, _ = wednesday.intake(bank_change(requester))
    assert "V08" in ids(case)
    assert case["approvals_needed"] == 2


def test_broken_iban_is_rejected(svc, requester):
    body = bank_change(requester, iban="DE89370400440532013001", vendor={"iban": "DE89370400440532013001"},
                       documents=[])
    case, _, links = svc.intake(body)
    assert case["status"] == "rejected"
    assert links == []


def test_clean_new_vendor_goes_through(svc, requester):
    new = iban("DE", "370400440532013000")
    body = {"type": "onboarding", "requester": requester, "sourceEmail": "info@rheinauen.example.de",
            "vendor": {"name": "Rheinauen Kaffeetechnik GmbH", "street": "Friedrich-Ebert-Allee 71",
                       "postalCode": "53113", "city": "Bonn", "country": "DE", "vatId": "DE136695976",
                       "iban": new},
            "documents": [pdf_doc([f"IBAN: {new}", "USt-IdNr.: DE136695976"])]}
    case, _, links = svc.intake(body)
    assert case["status"] == "approved", case["risks"]
    assert links == []
    assert (svc.store.home / "exports" / f"{case['ref']}.csv").exists()


def test_iban_used_by_someone_else(svc, requester):
    new = iban("DE", "500105170648489890")
    svc.stammdaten.iban_owners[new] = [{"object": "vendor", "id": "300555", "name": "Other GmbH"}]
    case, _, _ = svc.intake(bank_change(requester, iban=new))
    assert "V03" in ids(case)


def test_master_data_down_still_works(svc, requester):
    svc.stammdaten.down = True
    case, _, _ = svc.intake(bank_change(requester))
    assert "V01" in ids(case)
    assert case["status"] == "pending_approval"


def test_decide_rules():
    r = [checks.Risk("V04", "warning", "", "")]
    assert checks.decide("bank_change", r) == ("pending_approval", 1)
    assert checks.decide("onboarding", []) == ("approved", 0)
    assert checks.decide("onboarding", [checks.Risk("V01", "blocker", "", "")]) == ("rejected", 0)
    assert checks.decide("bank_change", r + [checks.Risk("V06", "error", "", "")]) == ("pending_approval", 2)


def test_sanctions_skips_persons_and_matches_variants():
    s = Screening(FIXTURES / "sanctions-sample.xml")
    assert len(s.entries) == 3  # two aliases of one company plus one other company
    assert s.check("Nordlicht Handelskontor")[0]["ref"] == "EU.9001.01"
    assert s.check("Ivan Testperson") == []
    assert s.check("Bäckerei Schmitz GmbH") == []
