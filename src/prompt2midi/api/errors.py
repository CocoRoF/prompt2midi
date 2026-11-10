"""One error format for the whole API: {"error": {"code", "message", "details"?}}."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers or {}


def body(code: str, message: str, details: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"error": err}


def _loc(loc: tuple) -> str:
    parts = [p for p in loc if p != "body"]
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
    return out or "$"


_HTTP_CODES = {404: ("not_found", "not found"), 405: ("method_not_allowed", "method not allowed")}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        details = [{"path": _loc(tuple(e["loc"])), "message": str(e["msg"])} for e in exc.errors()]
        return JSONResponse(body("invalid_input", "the request is not valid", details), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        code, msg = _HTTP_CODES.get(exc.status_code, ("http_error", "request failed"))
        return JSONResponse(body(code, msg), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(_req: Request, _exc: Exception):
        # never echo the exception text: it may contain request data
        return JSONResponse(body("internal_error", "unexpected server error"), status_code=500)
