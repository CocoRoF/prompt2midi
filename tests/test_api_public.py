import json

import pytest

from prompt2midi.providers.adapters import AdapterResult
from prompt2midi.validate import check_midi, validate_spec
from tests.api_helpers import A_KEY, FakeAdapter, Env
from tests.conftest import VALID_SPEC

PROMPT = "A calm piano piece in C major, 70 BPM"


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def gen(env, **body):
    body.setdefault("prompt", PROMPT)
    return env.client.post("/api/generate", json=body)


def test_healthz(env):
    assert env.client.get("/healthz").json() == {"ok": True}


def test_providers_by_default_only_rule_is_available(env):
    r = env.client.get("/api/providers").json()
    assert [(p["id"], p["available"]) for p in r] == [("rule", True), ("anthropic", False), ("openai", False)]
    assert all(set(p) == {"id", "label", "available"} for p in r), "no key information of any kind"


def test_generate_with_the_rule_provider_and_download(env):
    r = gen(env)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"id", "spec", "midi_url", "provider_used", "seed", "warnings", "attempts"}
    assert j["provider_used"] == "rule" and j["midi_url"] == f"/api/midi/{j['id']}.mid"
    assert j["attempts"] == [{"provider": "rule", "ok": True, "errors": []}]
    spec, report = validate_spec(j["spec"])
    assert spec is not None and report.ok and spec.tempo_bpm == 70
    m = env.client.get(j["midi_url"])
    assert m.status_code == 200 and m.headers["content-type"] == "audio/midi"
    assert m.headers["content-disposition"].startswith("attachment;") and m.content[:4] == b"MThd"
    assert check_midi(m.content, spec) == []


def test_same_request_same_midi_bytes(env):
    a, b = gen(env).json(), gen(env).json()
    assert a["id"] != b["id"] and a["seed"] == b["seed"]
    assert env.client.get(a["midi_url"]).content == env.client.get(b["midi_url"]).content
    c = gen(env, seed=7).json()
    assert env.client.get(c["midi_url"]).content != env.client.get(a["midi_url"]).content and c["seed"] == 7


@pytest.mark.parametrize("body,path", [
    ({"prompt": ""}, "prompt"), ({"prompt": "   "}, "prompt"), ({}, "prompt"), ({"prompt": "x" * 2001}, "prompt"),
    ({"prompt": "ok", "provider": "gemini"}, "provider"), ({"prompt": "ok", "seed": -1}, "seed"),
    ({"prompt": "ok", "seed": 2**32}, "seed"), ({"prompt": "ok", "extra": 1}, "extra"), ({"prompt": "ok", "fallback": "maybe"}, "fallback"),
    ({"prompt": 5}, "prompt"),
])
def test_invalid_input_has_the_error_format(env, body, path):
    r = env.client.post("/api/generate", json=body)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_input" and err["message"]
    assert any(path == d["path"] for d in err["details"]), err


def test_not_json_body(env):
    r = env.client.post("/api/generate", content=b"{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input"


def test_prompt_longer_than_the_admin_limit(env):
    env.admin()
    assert env.put("/api/admin/limits", {"max_prompt_chars": 50}).status_code == 200
    ok, bad = gen(env, prompt="x" * 50), gen(env, prompt="x" * 51)
    assert ok.status_code == 200 and bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_input" and bad.json()["error"]["details"][0]["path"] == "prompt"


def test_unknown_midi_ids_are_404(env):
    for mid in ("nope", "A" * 24, "..%2f..%2fetc%2fpasswd", "x" * 100):
        r = env.client.get(f"/api/midi/{mid}.mid")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_midi_expires_after_the_ttl(tmp_path):
    e = Env(tmp_path, midi_ttl=60)
    try:
        j = e.client.post("/api/generate", json={"prompt": PROMPT}).json()
        e.clock.advance(59)
        assert e.client.get(j["midi_url"]).status_code == 200
        e.clock.advance(2)
        r = e.client.get(j["midi_url"])
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    finally:
        e.close()


def test_midi_store_is_bounded(tmp_path):
    e = Env(tmp_path, midi_max=3)
    try:
        urls = [e.client.post("/api/generate", json={"prompt": PROMPT, "seed": i}).json()["midi_url"] for i in range(5)]
        status = [e.client.get(u).status_code for u in urls]
        assert status == [404, 404, 200, 200, 200]
    finally:
        e.close()


def test_unknown_routes_and_methods_use_the_error_format(env):
    r = env.client.get("/api/nothing")
    assert r.status_code == 404 and r.json() == {"error": {"code": "not_found", "message": "not found"}}
    r = env.client.get("/api/generate")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


# ---------------- LLM gating ----------------

def test_llm_is_off_for_the_public_by_default_and_never_silently_replaced(env):
    env.admin()
    env.configure_llm(public=False)
    assert [p["available"] for p in env.client.get("/api/providers").json()] == [True, False, False]
    r = gen(env, provider="anthropic")
    assert r.status_code == 403 and r.json()["error"]["code"] == "llm_disabled"


def test_default_provider_llm_but_public_llm_off_is_served_by_rules(env):
    env.admin()
    env.configure_llm(public=False)
    assert env.put("/api/admin/public", {"public_llm": False, "default_provider": "anthropic"}).status_code == 200
    j = gen(env).json()
    assert j["provider_used"] == "rule"


def test_llm_generation_uses_the_stored_key_and_model(env):
    ad = FakeAdapter()
    env.adapters["anthropic"] = ad
    env.admin()
    env.configure_llm(model="my-model-1")
    assert [(p["id"], p["available"]) for p in env.client.get("/api/providers").json()] == [("rule", True), ("anthropic", True), ("openai", False)]
    r = gen(env, provider="anthropic")
    assert r.status_code == 200 and r.json()["provider_used"] == "anthropic"
    assert ad.calls[0]["api_key"] == A_KEY and ad.calls[0]["model"] == "my-model-1"
    assert A_KEY not in r.text


@pytest.mark.parametrize("mutate", [
    lambda e: e.delete("/api/admin/providers/anthropic/key"),
    lambda e: e.put("/api/admin/providers/anthropic", {"enabled": False}),
    lambda e: e.put("/api/admin/providers/anthropic", {"model": ""}),
], ids=["no-key", "disabled", "no-model"])
def test_unavailable_llm_gives_503(env, mutate):
    env.adapters["anthropic"] = FakeAdapter()
    env.admin()
    env.configure_llm()
    assert mutate(env).status_code == 200
    r = gen(env, provider="anthropic")
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_unavailable"
    assert env.adapters["anthropic"].calls == []
    assert [p["available"] for p in env.client.get("/api/providers").json()][1] is False


def test_llm_failure_without_fallback_is_422_with_attempts(env):
    bad = AdapterResult("{not json", "end")
    env.adapters["anthropic"] = FakeAdapter(bad)
    env.admin()
    env.configure_llm()
    r = gen(env, provider="anthropic", fallback=False)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "generation_failed" and len(err["details"]["attempts"]) == 3
    assert all(a["provider"] == "anthropic" and a["ok"] is False and a["errors"] for a in err["details"]["attempts"])
    assert A_KEY not in r.text


def test_llm_failure_with_fallback_uses_rules(env):
    env.adapters["anthropic"] = FakeAdapter(AdapterResult("garbage", "end"))
    env.admin()
    env.configure_llm()
    j = gen(env, provider="anthropic").json()
    assert j["provider_used"] == "rule" and [a["provider"] for a in j["attempts"]] == ["anthropic"] * 3 + ["rule"]


def test_retries_follow_the_admin_limit(env):
    ad = FakeAdapter(AdapterResult("garbage", "end"))
    env.adapters["anthropic"] = ad
    env.admin()
    env.configure_llm()
    assert env.put("/api/admin/limits", {"max_retries": 0}).status_code == 200
    gen(env, provider="anthropic")
    assert len(ad.calls) == 1


# ---------------- limits ----------------

def test_rate_limit_per_ip_and_window(env):
    env.admin()
    assert env.put("/api/admin/limits", {"requests_per_minute": 3}).status_code == 200
    codes = [gen(env).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = gen(env)
    assert r.json()["error"]["code"] == "rate_limited" and r.headers["retry-after"] == "60"
    env.clock.advance(61)
    assert gen(env).status_code == 200


def test_rate_limit_ignores_x_forwarded_for_unless_trusted(tmp_path):
    e = Env(tmp_path)
    try:
        e.admin()
        e.put("/api/admin/limits", {"requests_per_minute": 2})
        codes = [e.client.post("/api/generate", json={"prompt": PROMPT}, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429], "spoofed headers must not create new buckets"
    finally:
        e.close()
    t = Env(tmp_path / "trusted", trust_proxy=True)
    try:
        t.admin()
        t.put("/api/admin/limits", {"requests_per_minute": 2})
        codes = [t.client.post("/api/generate", json={"prompt": PROMPT}, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 200]
    finally:
        t.close()


def test_invalid_requests_also_count_against_the_rate_limit(env):
    env.admin()
    env.put("/api/admin/limits", {"requests_per_minute": 2})
    assert [env.client.post("/api/generate", json={"prompt": "x" * 2000}).status_code for _ in range(1)] == [400]
    env.put("/api/admin/limits", {"max_prompt_chars": 10})
    codes = [gen(env, prompt="x" * 50).status_code for _ in range(3)]
    assert 429 in codes


def test_daily_llm_cap_counts_attempts_and_survives_restart(tmp_path):
    ad = FakeAdapter()
    e = Env(tmp_path, adapters={"anthropic": ad})
    try:
        e.admin()
        e.configure_llm()
        e.put("/api/admin/limits", {"daily_llm_cap": 2, "requests_per_minute": 100})
        assert [e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "anthropic"}).status_code for _ in range(3)] == [200, 200, 429]
        r = e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "anthropic"})
        assert r.json()["error"]["code"] == "daily_cap_reached"
        assert e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "rule"}).status_code == 200, "rule requests are not capped"
        assert len(ad.calls) == 2
        e.clock.advance(24 * 3600)
        assert e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "anthropic"}).status_code == 200, "a new day resets the cap"
    finally:
        e.close()
    # restart on the same data dir keeps the day's count
    ad2 = FakeAdapter()
    e2 = Env(tmp_path, adapters={"anthropic": ad2})
    e2.clock.now = e.clock.now
    try:
        e2.login()
        e2.client.cookies.clear()
        assert e2.state.store.usage_today(e2.state.today()) == 1
    finally:
        e2.close()


def test_failed_llm_attempts_use_up_the_cap(tmp_path):
    e = Env(tmp_path, adapters={"anthropic": FakeAdapter(AdapterResult("garbage", "end"))})
    try:
        e.admin()
        e.configure_llm()
        e.put("/api/admin/limits", {"daily_llm_cap": 3, "requests_per_minute": 100})
        assert e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "anthropic"}).status_code == 200  # 3 failed tries, then rules
        r = e.client.post("/api/generate", json={"prompt": PROMPT, "provider": "anthropic"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "daily_cap_reached"
    finally:
        e.close()


# ---------------- misc ----------------

def test_unexpected_errors_do_not_leak_details(tmp_path, monkeypatch):
    import prompt2midi.api.routes_public as rp

    def boom(*a, **k):
        raise RuntimeError("secret internal detail /etc/passwd")
    monkeypatch.setattr(rp, "generate", boom)
    e = Env(tmp_path, raise_server_exceptions=False)
    try:
        r = e.client.post("/api/generate", json={"prompt": PROMPT})
        assert r.status_code == 500 and r.json() == {"error": {"code": "internal_error", "message": "unexpected server error"}}
        assert "secret" not in r.text and "passwd" not in r.text
    finally:
        e.close()


def test_security_headers(env):
    for path in ("/healthz", "/", "/api/providers"):
        h = env.client.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY"
        csp = h["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp
    assert env.client.get("/api/admin/status").headers["cache-control"] == "no-store"


def test_static_screens_are_served_and_nothing_else(env):
    assert env.client.get("/").headers["content-type"].startswith("text/html")
    assert "prompt2midi" in env.client.get("/").text
    assert env.client.get("/admin").status_code == 200 and "관리자" in env.client.get("/admin").text
    for asset, ctype in (("/app.js", "javascript"), ("/common.js", "javascript"), ("/admin.js", "javascript"), ("/app.css", "text/css")):
        r = env.client.get(asset)
        assert r.status_code == 200 and ctype in r.headers["content-type"], asset
    for bad in ("/README.md", "/../pyproject.toml", "/..%2fpyproject.toml", "/%2e%2e/%2e%2e/etc/passwd", "/web/index.html", "/instance/admin.json", "/admin.json"):
        assert env.client.get(bad).status_code == 404, bad


def test_the_app_object_is_created_lazily(monkeypatch, tmp_path):
    monkeypatch.setenv("P2M_DATA_DIR", str(tmp_path / "lazy"))
    import importlib
    mod = importlib.import_module("prompt2midi.api.app")
    mod._APP = None
    assert not (tmp_path / "lazy").exists(), "importing must not create state"
    app = mod.app
    assert app is mod.app and (tmp_path / "lazy").is_dir()
    mod._APP = None


def test_web_pages_have_no_inline_style_or_script():
    """The CSP forbids inline styles/scripts; a style= attribute or inline <script> in web/ would silently break the screens."""
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "web"
    for page in web.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle\s*=", text, re.I), f"{page.name}: inline style attribute"
        assert not re.search(r"<style[\s>]", text, re.I), f"{page.name}: <style> element"
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", text, re.I), f"{page.name}: inline <script>"
        assert not re.search(r"\son[a-z]+\s*=", text, re.I), f"{page.name}: inline event handler"
