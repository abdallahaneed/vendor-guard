"""HTTP side: the API n8n calls, and the pages approvers open from their email.

Start with:  vendor-guard serve
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, pages
from .config import Settings
from .service import BadRequest, Service


def _error(status, code):
    async def handler(_: Request, e: Exception):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": str(e)}})
    return handler


def create_app(settings: Settings | None = None, service: Service | None = None):
    settings = settings or Settings()
    app = FastAPI(title="vendor-guard", version="0.1.0",
                  description="Checks vendor onboarding and bank detail changes before they reach the master data.")
    app.state.settings = settings
    app.state.svc = service or Service(settings)
    app.add_exception_handler(BadRequest, _error(422, "invalid_input"))
    app.add_exception_handler(PermissionError, _error(401, "unauthorized"))
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app
