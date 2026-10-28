"""Request models for the API and the shape of a case in API answers."""
from typing import Literal

from pydantic import BaseModel, Field


class Requester(BaseModel):
    name: str = Field("", max_length=80)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class VendorIn(BaseModel):
    name: str = Field("", max_length=80)
    name2: str = Field("", max_length=80)
    street: str = Field("", max_length=80)
    postalCode: str = Field("", max_length=10)
    city: str = Field("", max_length=40)
    country: str = Field("", max_length=2)
    vatId: str = Field("", max_length=20)
    iban: str = Field("", max_length=40)
    accountHolder: str = Field("", max_length=80)
    email: str = Field("", max_length=120)
    phone: str = Field("", max_length=40)


class Document(BaseModel):
    filename: str = Field(..., max_length=120)
    contentBase64: str = Field(..., max_length=10_000_000, description="PDF, base64 encoded, up to about 7 MB")


class CaseIn(BaseModel):
    type: Literal["onboarding", "bank_change"]
    requester: Requester
    sourceEmail: str = Field("", max_length=120, description="address the vendor's letter or email came from")
    existingId: str | None = Field(None, max_length=10, description="vendor number, needed for bank_change")
    vendor: VendorIn = VendorIn()
    documents: list[Document] = Field(default_factory=list, max_length=5)


class ResumeIn(BaseModel):
    url: str = Field(..., max_length=500)


def email_text(case, links):
    lines = [f"{case['ref']}: {case['type'].replace('_', ' ')} for {case['request']['vendor']['name']}",
             f"Requested by {case['requester_email']}", ""]
    if case["risks"]:
        lines.append("What the checks found:")
        lines += [f"- [{r['severity']}] {r['title']}: {r['detail']}" for r in case["risks"]]
    else:
        lines.append("The checks found nothing.")
    if case["approvals_needed"] > 1:
        lines += ["", "High risk: two different approvers must agree."]
    return "\n".join(lines)


def public(case, links=None):
    out = {
        "caseId": case["id"], "ref": case["ref"], "type": case["type"], "status": case["status"],
        "approvalsNeeded": case["approvals_needed"], "requester": case["requester_email"],
        "vendor": case["request"]["vendor"], "existingId": case["existing_id"],
        "risks": case["risks"], "extracted": case["extracted"], "decisions": case["approvals"],
        "exportFile": case["export_file"], "createdAt": case["created_at"], "decidedAt": case["decided_at"],
    }
    if links is not None:
        out["approvalLinks"] = links
        out["emailText"] = email_text(case, links)
    return out
