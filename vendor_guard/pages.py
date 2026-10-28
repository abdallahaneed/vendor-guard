"""Pages people open in a browser: case list, case detail, approval."""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .api import CaseId, Svc
from .security import TokenError
from .service import BadRequest

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# 2026-10-27T13:12:28+00:00 -> 2026-10-27 13:12 UTC
templates.env.filters["when"] = lambda iso: f"{iso[:10]} {iso[11:16]} UTC" if iso else ""

router = APIRouter(include_in_schema=False, default_response_class=HTMLResponse)


def message(request, title, text, status=200, case=None):
    return templates.TemplateResponse(request, "message.html", {"title": title, "text": text, "case": case}, status)


def approve_page(svc, request, token, error=None, form=None, status=200):
    try:
        data, link, case = svc.open_link(token)
    except TokenError as e:
        return message(request, "Link not valid", str(e), 403)
    done = bool(link["used_at"]) or case["status"] != "pending_approval"
    return templates.TemplateResponse(request, "approve.html", {
        "case": case, "approver": data["a"], "token": token, "done": done and not error,
        "error": error, "form": form or {}}, status)


@router.get("/")
def cases_page(svc: Svc, request: Request):
    return templates.TemplateResponse(request, "cases.html", {"cases": svc.store.list_cases()})


@router.get("/cases/{caseId}")
def case_page(svc: Svc, request: Request, case_id: CaseId):
    case = svc.store.get_case(case_id)
    if case is None:
        return message(request, "Not found", "This case does not exist.", 404)
    return templates.TemplateResponse(request, "case.html", {"case": case, "audit": svc.store.audit_for(case_id)})


@router.get("/approvals/{token}")
def approval_page(svc: Svc, request: Request, token: str):
    return approve_page(svc, request, token)


@router.post("/approvals/{token}")
def approval_submit(svc: Svc, request: Request, token: str,
                    decision: Annotated[str, Form()],
                    callback_phone: Annotated[str, Form(max_length=40)] = "",
                    comment: Annotated[str, Form(max_length=1000)] = ""):
    try:
        case = svc.decide(token, decision, callback_phone, comment)
    except TokenError as e:
        return message(request, "Link not valid", str(e), 403)
    except BadRequest as e:
        form = {"decision": decision, "callback_phone": callback_phone, "comment": comment}
        return approve_page(svc, request, token, str(e), form, 422)
    text = ("Thanks. One more approver has to agree before the change goes through."
            if case["status"] == "pending_approval"
            else f"Thanks. The case is now {case['status'].replace('_', ' ')}.")
    return message(request, case["ref"], text, case=case)
