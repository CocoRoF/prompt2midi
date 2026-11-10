"""Administrator API: first-time setup, login/logout, provider keys and limits (contract sections 3-4)."""
from __future__ import annotations

import hmac
import re
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from ..providers import LABELS, ProviderError
from .deps import SESSION_COOKIE, client_ip, require_admin, state
from .errors import ApiError
from .routes_public import make_provider
from .security import MAX_PASSWORD_CHARS, MIN_PASSWORD_CHARS, hash_password, verify_password
from .store import LIMIT_RANGES, MAX_KEY_CHARS, MIN_KEY_CHARS, PROVIDER_IDS

router = APIRouter(prefix="/api/admin")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,120}$")
KEY_RE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII without spaces


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupIn(_Strict):
    setup_token: Annotated[str, Field(min_length=1, max_length=200)]
    password: Annotated[str, Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)]


class LoginIn(_Strict):
    password: Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_CHARS)]


class ProviderIn(_Strict):
    api_key: StrictStr | None = Field(default=None, max_length=MAX_KEY_CHARS)
    model: StrictStr | None = Field(default=None, max_length=120)
    enabled: StrictBool | None = None

    @field_validator("api_key")
    @classmethod
    def _key(cls, v):
        if v is None or v == "":
            return None  # empty means "unchanged"
        if len(v) < MIN_KEY_CHARS or not KEY_RE.match(v):
            raise ValueError(f"API key must be {MIN_KEY_CHARS}-{MAX_KEY_CHARS} printable characters without spaces")
        return v

    @field_validator("model")
    @classmethod
    def _model(cls, v):
        if v is None:
            return None
        v = v.strip()
        if v and not MODEL_RE.match(v):
            raise ValueError("model name may contain letters, digits and . _ : / @ + -")
        return v


class LimitsIn(_Strict):
    requests_per_minute: StrictInt | None = None
    daily_llm_cap: StrictInt | None = None
    max_prompt_chars: StrictInt | None = None
    max_retries: StrictInt | None = None

    @model_validator(mode="after")
    def _ranges(self):
        given = {k: v for k, v in self.model_dump().items() if v is not None}
        if not given:
            raise ValueError("give at least one limit")
        for k, v in given.items():
            lo, hi = LIMIT_RANGES[k]
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
        return self


class PublicIn(_Strict):
    public_llm: StrictBool
    default_provider: Literal["rule", "anthropic", "openai"] | None = None


def _provider_block(st, pid: str) -> dict:
    cfg = st.store.settings()["providers"][pid]
    return {"key_set": bool(st.store.get_key(pid)), "key_hint": st.store.key_hint(pid), "model": cfg.get("model", ""), "enabled": bool(cfg.get("enabled"))}


def _settings_body(st) -> dict:
    cfg = st.store.settings()
    return {
        "providers": {pid: _provider_block(st, pid) for pid in PROVIDER_IDS},
        "limits": cfg["limits"],
        "public_llm": cfg["public_llm"],
        "default_provider": cfg["default_provider"],
        "storage": {"secrets_encrypted": st.store.secrets_encrypted},
    }


def _pid(pid: str) -> str:
    if pid not in PROVIDER_IDS:
        raise ApiError(404, "not_found", "unknown provider")
    return pid


# ---------- unauthenticated: status, setup, login ----------

@router.get("/status")
def status(request: Request) -> dict:
    return {"configured": state(request).store.admin_record() is not None}


def _too_many(st, ip: str) -> ApiError:
    return ApiError(429, "rate_limited", "too many failed attempts; try again later", headers={"Retry-After": str(int(st.cfg.login_window))})


@router.post("/setup")
def setup(body: SetupIn, request: Request) -> dict:
    st = state(request)
    ip = client_ip(request)
    if st.store.admin_record() is not None:
        raise ApiError(409, "already_configured", "the administrator is already set up")
    if st.login_fail.count(ip) >= st.cfg.login_max_failures:
        raise _too_many(st, ip)
    if not st.setup_token or not hmac.compare_digest(body.setup_token.encode("utf-8"), st.setup_token.encode("utf-8")):
        st.login_fail.record(ip)
        st.cfg.sleep(st.cfg.login_delay)
        raise ApiError(403, "invalid_setup_token", "the setup token is wrong")
    st.store.set_admin_record(hash_password(body.password))
    st.setup_token = None  # single use
    st.login_fail.reset(ip)
    return {"ok": True}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    st = state(request)
    ip = client_ip(request)
    rec = st.store.admin_record()
    if rec is None:
        raise ApiError(409, "not_configured", "the administrator has not been set up yet")
    if st.login_fail.count(ip) >= st.cfg.login_max_failures:
        raise _too_many(st, ip)
    if not verify_password(body.password, rec):
        st.login_fail.record(ip)
        st.cfg.sleep(st.cfg.login_delay)
        raise ApiError(401, "invalid_credentials", "wrong password")
    st.login_fail.reset(ip)
    token, csrf = st.sessions.create()
    secure = request.url.scheme == "https" or (st.cfg.trust_proxy and request.headers.get("x-forwarded-proto") == "https")
    response.set_cookie(SESSION_COOKIE, token, max_age=int(st.cfg.session_ttl), httponly=True, samesite="strict", secure=secure, path="/")
    return {"ok": True, "csrf_token": csrf}


# ---------- authenticated (session + CSRF for changes) ----------

@router.post("/logout")
def logout(request: Request, response: Response, _s: dict = Depends(require_admin)) -> dict:
    state(request).sessions.delete(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/settings")
def get_settings(request: Request, _s: dict = Depends(require_admin)) -> dict:
    return _settings_body(state(request))


@router.put("/providers/{pid}")
def put_provider(pid: str, body: ProviderIn, request: Request, _s: dict = Depends(require_admin)) -> dict:
    st = state(request)
    _pid(pid)
    if body.api_key is not None:
        st.store.set_key(pid, body.api_key)

    def apply(data):
        if body.model is not None:
            data["providers"][pid]["model"] = body.model
        if body.enabled is not None:
            data["providers"][pid]["enabled"] = body.enabled
    st.store.update_settings(apply)
    return {"ok": True, **_provider_block(st, pid)}


@router.delete("/providers/{pid}/key")
def delete_key(pid: str, request: Request, _s: dict = Depends(require_admin)) -> dict:
    st = state(request)
    _pid(pid)
    st.store.delete_key(pid)
    return {"ok": True, **_provider_block(st, pid)}


@router.post("/providers/{pid}/test")
def test_provider(pid: str, request: Request, _s: dict = Depends(require_admin)) -> dict:
    """One minimal real request, only when the administrator presses the button."""
    st = state(request)
    _pid(pid)
    prov = make_provider(st, pid)
    t0 = time.perf_counter()
    try:
        prov.ping()
        ok, message = True, f"{LABELS[pid]} answered"
    except ProviderError as exc:
        ok, message = False, st.scrub(str(exc))
    return {"ok": ok, "latency_ms": int((time.perf_counter() - t0) * 1000), "message": message}


@router.put("/limits")
def put_limits(body: LimitsIn, request: Request, _s: dict = Depends(require_admin)) -> dict:
    st = state(request)
    given = {k: v for k, v in body.model_dump().items() if v is not None}
    st.store.update_settings(lambda d: d["limits"].update(given))
    return {"ok": True, "limits": st.store.settings()["limits"]}


@router.put("/public")
def put_public(body: PublicIn, request: Request, _s: dict = Depends(require_admin)) -> dict:
    st = state(request)

    def apply(d):
        d["public_llm"] = body.public_llm
        if body.default_provider is not None:
            d["default_provider"] = body.default_provider
    d = st.store.update_settings(apply)
    return {"ok": True, "public_llm": d["public_llm"], "default_provider": d["default_provider"]}
