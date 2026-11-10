"""Shared helpers for the API tests (no network: fake SDK adapters, injectable clock)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from prompt2midi.api.app import create_app
from prompt2midi.api.config import AppSettings
from prompt2midi.providers.adapters import AdapterResult
from tests.conftest import VALID_SPEC

ADMIN_PASSWORD = "correct horse battery staple"
A_KEY = "sk-ant-api03-ZYXWVUTSRQPONMLK1234567890abc"  # pragma: allowlist secret
O_KEY = "sk-proj-LKJIHGFEDCBAzyxwvuts0987654321qrs"  # pragma: allowlist secret
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAdapter:
    """Stands in for the vendor SDK adapter: records calls, answers from `replies`."""

    def __init__(self, *replies):
        self.replies = list(replies) or [AdapterResult(json.dumps(VALID_SPEC), "end")]
        self.calls: list[dict] = []

    def complete_json(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Env:
    """An app instance + TestClient + handles on its internals."""

    def __init__(self, tmp_path, *, adapters=None, base_url="http://testserver", raise_server_exceptions=True, **cfg):
        self.clock = Clock()
        self.sleeps: list[float] = []
        defaults = dict(data_dir=tmp_path / "data", web_dir=WEB_DIR, clock=self.clock, sleep=self.sleeps.append)
        defaults.update(cfg)
        self.cfg = AppSettings(**defaults)
        self.adapters = adapters or {}
        self.app = create_app(self.cfg, self.adapters)
        self.state = self.app.state.p2m
        self.client = TestClient(self.app, base_url=base_url, raise_server_exceptions=raise_server_exceptions)
        self.client.__enter__()  # runs the lifespan (announces the setup token)
        self.csrf: str | None = None

    def close(self):
        self.client.__exit__(None, None, None)

    # --- admin helpers ---
    def setup_admin(self, password: str = ADMIN_PASSWORD):
        r = self.client.post("/api/admin/setup", json={"setup_token": self.state.setup_token, "password": password})
        assert r.status_code == 200, r.text
        return r

    def login(self, password: str = ADMIN_PASSWORD):
        r = self.client.post("/api/admin/login", json={"password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def admin(self):
        self.setup_admin()
        assert self.login().status_code == 200
        return self

    def h(self):
        return {"X-CSRF-Token": self.csrf}

    def put(self, path, body):
        return self.client.put(path, json=body, headers=self.h())

    def post(self, path, body=None):
        return self.client.post(path, json=body, headers=self.h())

    def delete(self, path):
        return self.client.delete(path, headers=self.h())

    def configure_llm(self, pid="anthropic", key=A_KEY, model="test-model", enabled=True, public=True):
        assert self.put(f"/api/admin/providers/{pid}", {"api_key": key, "model": model, "enabled": enabled}).status_code == 200
        if public:
            assert self.put("/api/admin/public", {"public_llm": True}).status_code == 200
