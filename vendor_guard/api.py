"""API for n8n and other systems. Service and settings come from app.state."""
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from .models import CaseIn, ResumeIn, public
from .service import Service

CaseId = Annotated[int, Path(alias="caseId", ge=1)]
key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_service(request: Request) -> Service:
    return request.app.state.svc


def require_key(request: Request, x_api_key: str | None = Depends(key_header)):
    key = request.app.state.settings.api_key
    if key and not secrets.compare_digest(x_api_key or "", key):
        raise PermissionError("missing or wrong X-API-Key")


Svc = Annotated[Service, Depends(get_service)]
writes = [Depends(require_key)]
router = APIRouter(prefix="/api/v1")


def not_found():
    return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "no such case"}})


@router.get("/health", tags=["system"])
def health(svc: Svc):
    return {"status": "ok", "sanctionsList": svc.screening.source, "sanctionsEntries": len(svc.screening.entries)}


@router.post("/cases", tags=["cases"], status_code=201, dependencies=writes,
             summary="Check a vendor request and open a case")
def create_case(svc: Svc, body: CaseIn, response: Response,
                idempotency_key: Annotated[str | None, Header(max_length=100,
                                                              description="same key, same case: safe to retry")] = None):
    """Runs every check and decides: approved, rejected, or pending_approval with one
    approval link per approver. Send the links by email, then wait for the decision."""
    case, created, links = svc.intake(body.model_dump(), idempotency_key)
    if not created:
        response.status_code = 200
    return public(case, links)


@router.post("/cases/{caseId}/resume-url", tags=["cases"], status_code=204, dependencies=writes,
             summary="Tell vendor-guard where to send the decision")
def set_resume(svc: Svc, case_id: CaseId, body: ResumeIn):
    """n8n calls this with $execution.resumeUrl before its Wait node starts waiting."""
    if svc.store.get_case(case_id) is None:
        return not_found()
    svc.store.set_resume_url(case_id, body.url)
    return Response(status_code=204)


@router.get("/cases", tags=["cases"])
def list_cases(svc: Svc):
    return svc.store.list_cases()


@router.get("/cases/{caseId}", tags=["cases"])
def get_case(svc: Svc, case_id: CaseId):
    case = svc.store.get_case(case_id)
    if case is None:
        return not_found()
    return {**public(case), "audit": svc.store.audit_for(case_id)}


@router.get("/audit/head", tags=["audit"], summary="Latest audit hash, to store somewhere else")
def audit_head(svc: Svc):
    return svc.store.audit_head()


@router.get("/audit/verify", tags=["audit"], summary="Recompute the whole audit chain")
def audit_verify(svc: Svc):
    ok, n, broken = svc.store.verify_audit()
    return {"ok": ok, "entries": n, "firstBrokenId": broken}
