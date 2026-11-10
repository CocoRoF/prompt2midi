"""Shared request helpers."""
from __future__ import annotations

import hmac

from fastapi import Request

from .errors import ApiError

SESSION_COOKIE = "p2m_session"


def state(request: Request):
    return request.app.state.p2m


def client_ip(request: Request) -> str:
    st = state(request)
    if st.cfg.trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            return xff.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "unknown"


def require_admin(request: Request) -> dict:
    """Valid session cookie, plus a matching X-CSRF-Token for every non-GET request."""
    st = state(request)
    session = st.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise ApiError(401, "unauthorized", "administrator login required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent.encode("utf-8"), session["csrf"].encode("utf-8")):
            raise ApiError(403, "csrf_failed", "missing or wrong CSRF token")
    return session
