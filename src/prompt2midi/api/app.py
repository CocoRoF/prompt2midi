"""FastAPI application (entry point: prompt2midi.api.app:app). Contract: docs/api-and-admin.md."""
from __future__ import annotations

import contextlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from ..providers.secrets import mask_secrets
from . import errors
from .config import AppSettings
from .midistore import MidiStore
from .routes_admin import router as admin_router
from .routes_public import router as public_router
from .security import SlidingWindow, Sessions
from .store import Store

log = logging.getLogger("prompt2midi.api")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
STATIC_SUFFIXES = (".html", ".js", ".css")


@dataclass
class State:
    cfg: AppSettings
    store: Store
    sessions: Sessions
    midis: MidiStore
    rate: SlidingWindow  # /api/generate per IP, 60 s window
    login_fail: SlidingWindow  # failed admin logins/setups per IP
    setup_token: str | None
    setup_announced: bool = False
    adapters: dict | None = None  # tests: {provider_id: fake SDK adapter}

    def today(self) -> str:
        return datetime.fromtimestamp(self.cfg.clock(), timezone.utc).strftime("%Y-%m-%d")

    def scrub(self, text: str) -> str:
        """Last line of defence: remove any stored API key (or a fragment of one) from outgoing text."""
        return mask_secrets(text, list(self.store.keys().values()))


def create_app(cfg: AppSettings | None = None, adapters: dict | None = None) -> FastAPI:
    cfg = cfg or AppSettings.from_env()
    for noisy in ("anthropic", "openai", "httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # DEBUG logs of the SDKs may contain request details

    store = Store(cfg)
    state = State(
        cfg=cfg, store=store, sessions=Sessions(cfg.session_ttl, cfg.clock), midis=MidiStore(cfg.midi_ttl, cfg.midi_max, cfg.clock),
        rate=SlidingWindow(60.0, cfg.clock), login_fail=SlidingWindow(cfg.login_window, cfg.clock),
        setup_token=None if store.admin_record() else secrets.token_urlsafe(24), adapters=adapters,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if state.setup_token and not state.setup_announced:
            state.setup_announced = True
            print(f"[prompt2midi] 관리자 최초 설정 토큰(1회용, 이 줄은 한 번만 출력됩니다): {state.setup_token}", flush=True)
        yield

    app = FastAPI(title="prompt2midi", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.p2m = state
    errors.install(app)
    app.include_router(public_router)
    app.include_router(admin_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/admin"):
            h["Cache-Control"] = "no-store"
        return resp

    _mount_static(app, cfg)
    return app


def _mount_static(app: FastAPI, cfg: AppSettings) -> None:
    web = cfg.web_dir
    if not web.is_dir():
        log.warning("web directory %s not found: screens are not served", web)
        return
    files = {p.name: p for p in web.iterdir() if p.is_file() and p.suffix in STATIC_SUFFIXES}

    def serve(name: str):
        path = files[name]

        async def handler() -> Response:
            return FileResponse(path, headers={"Cache-Control": "no-cache"})
        return handler

    for name in files:
        app.add_api_route("/" + name, serve(name), methods=["GET"], include_in_schema=False)
    if "index.html" in files:
        app.add_api_route("/", serve("index.html"), methods=["GET"], include_in_schema=False)
    if "admin.html" in files:
        app.add_api_route("/admin", serve("admin.html"), methods=["GET"], include_in_schema=False)


def __getattr__(name: str) -> Any:  # `uvicorn prompt2midi.api.app:app` creates the app on first access (no import side effects)
    if name == "app":
        global _APP
        if _APP is None:
            _APP = create_app()
        return _APP
    raise AttributeError(name)


_APP: FastAPI | None = None
