import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import VENDOR
from test_checks import bank_change

from vendor_guard.app import create_app
from vendor_guard.security import make_token


@pytest.fixture
def client(svc, settings):
    return TestClient(create_app(settings, svc))


def post_case(client, body, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/v1/cases", json=body, headers=headers)


def token_of(link):
    return link["url"].rsplit("/", 1)[1]


def test_idempotent_intake(client, requester):
    body = bank_change(requester)
    a = post_case(client, body, key="abc-1")
    b = post_case(client, body, key="abc-1")
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()["caseId"] == b.json()["caseId"]
    assert len(client.get("/api/v1/cases").json()) == 1


def test_bank_change_needs_the_number_on_file(client, requester, resumed, svc):
    case = post_case(client, bank_change(requester)).json()
    assert case["status"] == "pending_approval"
    client.post(f"/api/v1/cases/{case['caseId']}/resume-url", json={"url": "http://n8n/webhook-waiting/7"})
    tok = token_of(case["approvalLinks"][0])

    page = client.get(f"/approvals/{tok}")
    assert page.status_code == 200
    assert VENDOR["phone"] in page.text

    wrong = client.post(f"/approvals/{tok}", data={"decision": "approve", "callback_phone": "+49 1577 3920114"})
    assert wrong.status_code == 422
    assert "number on file" in wrong.text

    ok = client.post(f"/approvals/{tok}", data={"decision": "approve", "callback_phone": "0228 4171864"})
    assert ok.status_code == 200
    done = client.get(f"/api/v1/cases/{case['caseId']}").json()
    assert done["status"] == "approved"
    assert done["exportFile"] == f"{case['ref']}.csv"
    assert (svc.store.home / "exports" / done["exportFile"]).exists()

    # n8n was told
    assert resumed[-1][0] == "http://n8n/webhook-waiting/7"
    assert resumed[-1][1]["status"] == "approved"

    # the link works once
    again = client.post(f"/approvals/{tok}", data={"decision": "approve", "callback_phone": "0228 4171864"})
    assert again.status_code == 422


def test_reject_needs_a_reason(client, requester):
    case = post_case(client, bank_change(requester)).json()
    tok = token_of(case["approvalLinks"][0])
    r = client.post(f"/approvals/{tok}", data={"decision": "reject"})
    assert r.status_code == 422
    r = client.post(f"/approvals/{tok}", data={"decision": "reject", "comment": "vendor denied it on the phone"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/cases/{case['caseId']}").json()["status"] == "rejected"


def test_high_risk_needs_two_people(client, requester):
    body = bank_change(requester, sourceEmail="someone@freemail.example.com")
    case = post_case(client, body).json()
    assert case["approvalsNeeded"] == 2
    t1, t2 = (token_of(l) for l in case["approvalLinks"])
    client.post(f"/approvals/{t1}", data={"decision": "approve", "callback_phone": VENDOR["phone"]})
    assert client.get(f"/api/v1/cases/{case['caseId']}").json()["status"] == "pending_approval"
    client.post(f"/approvals/{t2}", data={"decision": "approve", "callback_phone": VENDOR["phone"]})
    assert client.get(f"/api/v1/cases/{case['caseId']}").json()["status"] == "approved"


def test_forged_and_expired_links(client, requester, settings):
    case = post_case(client, bank_change(requester)).json()
    tok = token_of(case["approvalLinks"][0])
    forged = tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")
    assert client.get(f"/approvals/{forged}").status_code == 403
    expired, _, _ = make_token(settings.link_secret, case["caseId"], "anna@example.com", hours=-1)
    assert client.get(f"/approvals/{expired}").status_code == 403
    # signed correctly, but never issued
    unknown, _, _ = make_token(settings.link_secret, case["caseId"], "anna@example.com", hours=1)
    assert client.get(f"/approvals/{unknown}").status_code == 403


def test_audit_chain_detects_tampering(client, requester, svc):
    post_case(client, bank_change(requester))
    assert client.get("/api/v1/audit/verify").json()["ok"] is True
    con = sqlite3.connect(svc.store.path)
    con.execute("UPDATE audit SET data = replace(data, 'DE', 'NL') WHERE action = 'case_created'")
    con.commit()
    con.close()
    r = client.get("/api/v1/audit/verify").json()
    assert r["ok"] is False and r["firstBrokenId"] == 1


def test_resume_url_and_api_key(svc, settings, requester):
    settings.api_key = "k1"
    c = TestClient(create_app(settings, svc))
    assert c.post("/api/v1/cases", json=bank_change(requester)).status_code == 401
    case = c.post("/api/v1/cases", json=bank_change(requester), headers={"X-API-Key": "k1"}).json()
    r = c.post(f"/api/v1/cases/{case['caseId']}/resume-url", json={"url": "http://n8n/webhook-waiting/1"},
               headers={"X-API-Key": "k1"})
    assert r.status_code == 204
    assert svc.store.get_case(case["caseId"])["resume_url"].endswith("/1")


def test_unknown_vendor_and_bad_input(client, requester):
    r = post_case(client, bank_change(requester, existingId="399999"))
    assert r.status_code == 422 and "not found" in r.json()["error"]["message"]
    r = post_case(client, {"type": "bank_change", "requester": requester, "vendor": {}})
    assert r.status_code == 422
    r = post_case(client, {"type": "onboarding", "requester": {"email": "not-an-email"}})
    assert r.status_code == 422


def test_refuses_to_start_with_weak_secrets(settings):
    for secret in ("", "change-me-link-secret-0123456789abcdef", "short"):
        settings.link_secret = secret
        with pytest.raises(SystemExit, match="LINK_SECRET"):
            settings.check_secrets()
    settings.link_secret = settings.audit_key = "a" * 64
    settings.check_secrets()
