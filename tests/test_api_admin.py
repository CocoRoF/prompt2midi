import json
import logging
import os
import stat

import pytest

from prompt2midi.providers.adapters import AdapterResult
from tests.api_helpers import ADMIN_PASSWORD, A_KEY, O_KEY, Env, FakeAdapter


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------- first-time setup ----------------

def test_status_and_setup_token_is_printed_once(tmp_path, capsys):
    e = Env(tmp_path)
    try:
        out = capsys.readouterr().out
        assert e.client.get("/api/admin/status").json() == {"configured": False}
        assert out.count(e.state.setup_token) == 1 and "설정 토큰" in out
        e.setup_admin()
        assert e.client.get("/api/admin/status").json() == {"configured": True}
        assert e.state.setup_token is None
    finally:
        e.close()
    again = Env(tmp_path)  # restart after setup: no token is generated or printed
    try:
        assert again.state.setup_token is None
        assert "설정 토큰" not in capsys.readouterr().out
    finally:
        again.close()


def test_setup_token_is_random_per_process(tmp_path):
    a, b = Env(tmp_path / "a"), Env(tmp_path / "b")
    try:
        assert a.state.setup_token != b.state.setup_token and len(a.state.setup_token) >= 24
    finally:
        a.close(), b.close()


def test_setup_rejects_wrong_token_and_short_password(env):
    r = env.client.post("/api/admin/setup", json={"setup_token": "wrong", "password": ADMIN_PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_setup_token"
    r = env.client.post("/api/admin/setup", json={"setup_token": env.state.setup_token, "password": "short"})
    assert r.status_code == 400 and r.json()["error"]["details"][0]["path"] == "password"
    assert env.client.get("/api/admin/status").json() == {"configured": False}, "failed attempts must not configure anything"


def test_setup_happens_exactly_once(env):
    token = env.state.setup_token
    env.setup_admin()
    r = env.client.post("/api/admin/setup", json={"setup_token": token, "password": "another very long password"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_configured"
    assert env.login("another very long password").status_code == 401, "the first password must still be the only one"
    assert env.login().status_code == 200


def test_setup_token_is_single_use_even_if_the_state_is_reset(env):
    token = env.state.setup_token
    env.setup_admin()
    env.state.setup_token = token  # an attacker replaying the old token still gets 409 (configured)
    r = env.client.post("/api/admin/setup", json={"setup_token": token, "password": "x" * 20})
    assert r.status_code == 409


def test_password_is_stored_as_a_salted_scrypt_hash(env):
    env.setup_admin()
    path = env.cfg.data_dir / "admin.json"
    rec = json.loads(path.read_text())
    assert rec["algo"] == "scrypt" and rec["n"] >= 2**14 and len(rec["salt"]) >= 20
    assert ADMIN_PASSWORD not in path.read_text()
    assert mode(path) == 0o600 and mode(env.cfg.data_dir) == 0o700
    env2 = Env(env.cfg.data_dir.parent / "other")
    try:
        env2.setup_admin()
        assert json.loads((env2.cfg.data_dir / "admin.json").read_text())["hash"] != rec["hash"], "salted: same password, different hash"
    finally:
        env2.close()


def test_setup_attempts_are_rate_limited(env):
    for _ in range(5):
        assert env.client.post("/api/admin/setup", json={"setup_token": "nope", "password": ADMIN_PASSWORD}).status_code == 403
    r = env.client.post("/api/admin/setup", json={"setup_token": env.state.setup_token, "password": ADMIN_PASSWORD})
    assert r.status_code == 429


# ---------------- login ----------------

def test_login_before_setup_is_409(env):
    r = env.login()
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"


def test_wrong_password_401_with_delay(env):
    env.setup_admin()
    r = env.login("this is not the password")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert env.sleeps == [env.cfg.login_delay] and "set-cookie" not in r.headers


def test_login_cookie_flags_and_csrf_token(env):
    env.setup_admin()
    r = env.login()
    assert r.status_code == 200 and len(r.json()["csrf_token"]) >= 32
    cookie = r.headers["set-cookie"]
    low = cookie.lower()
    assert "p2m_session=" in cookie and "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert "secure" not in low, "plain HTTP: no Secure flag"
    assert "max-age=43200" in low


def test_cookie_is_secure_over_https(tmp_path):
    e = Env(tmp_path, base_url="https://testserver")
    try:
        e.setup_admin()
        assert "secure" in e.login().headers["set-cookie"].lower()
    finally:
        e.close()


def test_failed_logins_lock_the_ip_out_and_recover(env):
    env.setup_admin()
    assert [env.login("wrong-password-123").status_code for _ in range(5)] == [401] * 5
    r = env.login()  # even the right password is refused now
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    env.clock.advance(601)
    assert env.login().status_code == 200


def test_a_successful_login_resets_the_failure_count(env):
    env.setup_admin()
    for _ in range(4):
        env.login("wrong-password-123")
    assert env.login().status_code == 200
    for _ in range(4):
        assert env.login("wrong-password-123").status_code == 401


# ---------------- authentication, CSRF, sessions ----------------

ADMIN_CALLS = [
    ("GET", "/api/admin/settings", None), ("POST", "/api/admin/logout", None),
    ("PUT", "/api/admin/providers/anthropic", {"model": "m"}), ("DELETE", "/api/admin/providers/anthropic/key", None),
    ("POST", "/api/admin/providers/anthropic/test", None), ("PUT", "/api/admin/limits", {"max_retries": 1}),
    ("PUT", "/api/admin/public", {"public_llm": False}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=[f"{m} {p}" for m, p, _ in ADMIN_CALLS])
def test_every_admin_endpoint_requires_a_session(env, method, path, body):
    env.setup_admin()
    r = env.client.request(method, path, json=body)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("method,path,body", [c for c in ADMIN_CALLS if c[0] != "GET"], ids=lambda x: str(x)[:30])
def test_every_change_requires_the_csrf_token(env, method, path, body):
    env.admin()
    r = env.client.request(method, path, json=body)  # session cookie present, header missing
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = env.client.request(method, path, json=body, headers={"X-CSRF-Token": "wrong" + env.csrf})
    assert r.status_code == 403
    r = env.client.request(method, path, json=body, headers={"X-CSRF-Token": ""})
    assert r.status_code == 403


def test_csrf_token_belongs_to_its_session(tmp_path):
    e = Env(tmp_path)
    try:
        e.admin()
        first_csrf = e.csrf
        e.client.cookies.clear()
        e.login()  # second session, different token
        assert e.csrf != first_csrf
        r = e.client.put("/api/admin/limits", json={"max_retries": 1}, headers={"X-CSRF-Token": first_csrf})
        assert r.status_code == 403
        assert e.put("/api/admin/limits", {"max_retries": 1}).status_code == 200
    finally:
        e.close()


def test_get_settings_needs_no_csrf(env):
    env.admin()
    assert env.client.get("/api/admin/settings").status_code == 200


def test_session_expires_after_twelve_hours(env):
    env.admin()
    env.clock.advance(12 * 3600 - 1)
    assert env.client.get("/api/admin/settings").status_code == 200
    env.clock.advance(2)
    r = env.client.get("/api/admin/settings")
    assert r.status_code == 401
    assert env.put("/api/admin/limits", {"max_retries": 1}).status_code == 401, "expired sessions cannot change anything"
    assert env.login().status_code == 200 and env.client.get("/api/admin/settings").status_code == 200


def test_logout_ends_the_session(env):
    env.admin()
    old_cookie = env.client.cookies.get("p2m_session")
    assert env.post("/api/admin/logout").status_code == 200
    env.client.cookies.set("p2m_session", old_cookie)  # replaying the old cookie
    assert env.client.get("/api/admin/settings").status_code == 401


def test_forged_or_garbage_cookies_are_rejected(env):
    env.setup_admin()
    for v in ("", "x", "A" * 43, "../../etc/passwd"):
        env.client.cookies.set("p2m_session", v)
        assert env.client.get("/api/admin/settings").status_code == 401


# ---------------- settings, keys ----------------

def test_settings_defaults_match_the_contract(env):
    env.admin()
    s = env.client.get("/api/admin/settings").json()
    assert set(s) == {"providers", "limits", "public_llm", "default_provider", "storage"}
    assert s["providers"]["anthropic"] == {"key_set": False, "key_hint": "", "model": "", "enabled": False}
    assert s["providers"].keys() == {"anthropic", "openai"}
    assert s["limits"] == {"requests_per_minute": 10, "daily_llm_cap": 100, "max_prompt_chars": 1000, "max_retries": 2}
    assert s["public_llm"] is False and s["default_provider"] == "rule" and s["storage"] == {"secrets_encrypted": False}


def test_saving_a_key_shows_only_the_last_four_characters(env):
    env.admin()
    r = env.put("/api/admin/providers/anthropic", {"api_key": A_KEY, "model": "  model-x  ", "enabled": True})
    assert r.status_code == 200
    s = env.client.get("/api/admin/settings").json()["providers"]["anthropic"]
    assert s == {"key_set": True, "key_hint": "…" + A_KEY[-4:], "model": "model-x", "enabled": True}


def test_empty_api_key_means_unchanged(env):
    env.admin()
    env.put("/api/admin/providers/openai", {"api_key": O_KEY, "model": "m1"})
    for empty in ("", None):
        assert env.put("/api/admin/providers/openai", {"api_key": empty, "model": "m2"}).status_code == 200
    assert env.state.store.get_key("openai") == O_KEY
    assert env.client.get("/api/admin/settings").json()["providers"]["openai"]["model"] == "m2"


def test_deleting_a_key(env):
    env.admin()
    env.put("/api/admin/providers/anthropic", {"api_key": A_KEY})
    assert env.delete("/api/admin/providers/anthropic/key").status_code == 200
    assert env.state.store.get_key("anthropic") is None
    assert A_KEY not in (env.cfg.data_dir / "secrets.json").read_text()
    assert env.client.get("/api/admin/settings").json()["providers"]["anthropic"]["key_set"] is False


@pytest.mark.parametrize("body", [
    {"api_key": "short"}, {"api_key": "has a space in the key 12345678"}, {"api_key": "x" * 513}, {"api_key": "key\nwith\nnewlines 12345"},
    {"model": "bad model!"}, {"model": "x" * 121}, {"enabled": "yes"}, {"extra": 1}, {"model": 5},
])
def test_provider_input_is_validated(env, body):
    env.admin()
    r = env.put("/api/admin/providers/anthropic", body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input"
    secret = body.get("api_key")
    if isinstance(secret, str) and len(secret) > 8:
        assert secret not in r.text, "validation errors must not echo the submitted key"


def test_unknown_provider_ids_are_404(env):
    env.admin()
    assert env.put("/api/admin/providers/gemini", {"model": "m"}).status_code == 404
    assert env.delete("/api/admin/providers/rule/key").status_code == 404
    assert env.post("/api/admin/providers/rule/test").status_code == 404


@pytest.mark.parametrize("field,lo,hi", [("requests_per_minute", 1, 600), ("daily_llm_cap", 0, 100000), ("max_prompt_chars", 1, 2000), ("max_retries", 0, 5)])
def test_limits_ranges(env, field, lo, hi):
    env.admin()
    for ok in (lo, hi):
        assert env.put("/api/admin/limits", {field: ok}).status_code == 200
        assert env.client.get("/api/admin/settings").json()["limits"][field] == ok
    for bad in (lo - 1, hi + 1, "5", 1.5, None):
        r = env.put("/api/admin/limits", {field: bad})
        assert r.status_code == 400, (field, bad)


def test_limits_empty_or_unknown_fields_are_rejected(env):
    env.admin()
    assert env.put("/api/admin/limits", {}).status_code == 400
    assert env.put("/api/admin/limits", {"nope": 1}).status_code == 400
    assert env.put("/api/admin/limits", {"max_retries": 1, "requests_per_minute": 20}).json()["limits"]["requests_per_minute"] == 20


def test_public_settings(env):
    env.admin()
    r = env.put("/api/admin/public", {"public_llm": True, "default_provider": "openai"})
    assert r.json() == {"ok": True, "public_llm": True, "default_provider": "openai"}
    assert env.put("/api/admin/public", {"public_llm": True, "default_provider": "gemini"}).status_code == 400
    assert env.put("/api/admin/public", {}).status_code == 400
    assert env.put("/api/admin/public", {"public_llm": False}).json()["default_provider"] == "openai", "default_provider unchanged when omitted"


def test_settings_persist_across_restarts(tmp_path):
    e = Env(tmp_path)
    try:
        e.admin()
        e.configure_llm("openai", O_KEY, "m-persist")
        e.put("/api/admin/limits", {"requests_per_minute": 7})
    finally:
        e.close()
    e2 = Env(tmp_path)
    try:
        assert e2.login().status_code == 200
        s = e2.client.get("/api/admin/settings").json()
        assert s["providers"]["openai"]["model"] == "m-persist" and s["providers"]["openai"]["key_set"] and s["limits"]["requests_per_minute"] == 7 and s["public_llm"] is True
    finally:
        e2.close()


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    e = Env(tmp_path)
    try:
        e.admin()
        (e.cfg.data_dir / "settings.json").write_text("{broken")
        assert e.client.get("/api/admin/settings").json()["limits"]["requests_per_minute"] == 10
    finally:
        e.close()


# ---------------- key storage ----------------

def test_plain_storage_is_0600_and_flagged_unencrypted(env):
    env.admin()
    env.put("/api/admin/providers/anthropic", {"api_key": A_KEY})
    path = env.cfg.data_dir / "secrets.json"
    assert mode(path) == 0o600 and json.loads(path.read_text())["encrypted"] is False
    assert env.client.get("/api/admin/settings").json()["storage"]["secrets_encrypted"] is False
    assert not list(env.cfg.data_dir.glob("*.tmp")), "no temp files left behind"
    for f in env.cfg.data_dir.iterdir():
        assert mode(f) == 0o600, f


def test_encrypted_storage_with_a_secret_key(tmp_path):
    e = Env(tmp_path, secret_key="a long operator supplied secret")
    try:
        e.admin()
        e.put("/api/admin/providers/anthropic", {"api_key": A_KEY, "model": "m", "enabled": True})
        raw = (e.cfg.data_dir / "secrets.json").read_text()
        assert A_KEY not in raw and A_KEY[10:26] not in raw and json.loads(raw)["encrypted"] is True
        assert mode(e.cfg.data_dir / "secrets.json") == 0o600
        assert e.client.get("/api/admin/settings").json()["storage"]["secrets_encrypted"] is True
    finally:
        e.close()
    again = Env(tmp_path, secret_key="a long operator supplied secret")
    try:
        assert again.state.store.get_key("anthropic") == A_KEY
    finally:
        again.close()


def test_wrong_or_missing_secret_key_makes_stored_keys_unavailable_without_leaking(tmp_path, caplog):
    e = Env(tmp_path, secret_key="first secret value")
    try:
        e.admin()
        e.put("/api/admin/providers/anthropic", {"api_key": A_KEY})
    finally:
        e.close()
    caplog.set_level(logging.DEBUG)
    for key in ("a different secret", None):
        other = Env(tmp_path, secret_key=key)
        try:
            assert other.state.store.get_key("anthropic") is None
            other.login()
            assert other.client.get("/api/admin/settings").json()["providers"]["anthropic"]["key_set"] is False
        finally:
            other.close()
    assert "unavailable" in caplog.text and A_KEY not in caplog.text and "first secret value" not in caplog.text


def test_plaintext_keys_are_encrypted_on_the_next_write(tmp_path):
    e = Env(tmp_path)
    try:
        e.admin()
        e.put("/api/admin/providers/anthropic", {"api_key": A_KEY})
    finally:
        e.close()
    e2 = Env(tmp_path, secret_key="now there is a secret")
    try:
        e2.login()
        assert e2.state.store.get_key("anthropic") == A_KEY, "plain file is still readable"
        e2.put("/api/admin/providers/openai", {"api_key": O_KEY})
        raw = (e2.cfg.data_dir / "secrets.json").read_text()
        assert A_KEY not in raw and O_KEY not in raw and e2.state.store.get_key("anthropic") == A_KEY
    finally:
        e2.close()


# ---------------- connection test button ----------------

def test_connection_test_calls_the_provider_exactly_when_pressed(env):
    ad = FakeAdapter(AdapterResult('{"ok": true}', "end"))
    env.adapters["anthropic"] = ad
    env.admin()
    env.configure_llm()
    env.client.get("/api/providers"), env.client.get("/api/admin/settings"), env.client.post("/api/generate", json={"prompt": "calm piano"})
    assert ad.calls == [], "nothing but the button may cause a paid call"
    r = env.post("/api/admin/providers/anthropic/test")
    assert r.status_code == 200 and r.json()["ok"] is True and isinstance(r.json()["latency_ms"], int)
    assert len(ad.calls) == 1 and ad.calls[0]["max_tokens"] <= 64 and ad.calls[0]["api_key"] == A_KEY


def test_connection_test_failures_are_reported_without_the_key(env):
    ad = FakeAdapter(RuntimeError(f"401 invalid x-api-key {A_KEY}"))
    env.adapters["anthropic"] = ad
    env.admin()
    env.configure_llm()
    r = env.post("/api/admin/providers/anthropic/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "RuntimeError" in r.json()["message"]
    assert A_KEY not in r.text and A_KEY[8:24] not in r.text


def test_connection_test_without_key_or_model(env):
    env.admin()
    r = env.post("/api/admin/providers/openai/test")
    assert r.json()["ok"] is False and "no API key" in r.json()["message"]
    env.put("/api/admin/providers/openai", {"api_key": O_KEY})
    assert "no model" in env.post("/api/admin/providers/openai/test").json()["message"]


# ---------------- keys never leave the server ----------------

def test_the_key_appears_in_no_response_log_or_output(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    ad = FakeAdapter(RuntimeError(f"auth failed for {A_KEY} Bearer {A_KEY}"))
    e = Env(tmp_path, adapters={"anthropic": ad})
    texts = []

    def rec(r):
        texts.append(r.text + json.dumps(dict(r.headers)))
        return r
    try:
        rec(e.client.get("/api/admin/status"))
        e.setup_admin()
        rec(e.login())
        rec(e.put("/api/admin/providers/anthropic", {"api_key": A_KEY, "model": "m", "enabled": True}))
        rec(e.put("/api/admin/public", {"public_llm": True}))
        rec(e.put("/api/admin/providers/openai", {"api_key": "bad key with spaces " + A_KEY}))  # invalid, contains the key text
        rec(e.client.get("/api/admin/settings"))
        rec(e.client.get("/api/providers"))
        rec(e.client.post("/api/generate", json={"prompt": "calm piano", "provider": "anthropic", "fallback": False}))
        rec(e.client.post("/api/generate", json={"prompt": "calm piano", "provider": "anthropic"}))
        rec(e.post("/api/admin/providers/anthropic/test"))
        rec(e.client.get("/"))
        rec(e.client.get("/admin"))
        rec(e.delete("/api/admin/providers/anthropic/key"))
        everything = "\n".join(texts) + caplog.text + capsys.readouterr().out + capsys.readouterr().err
        for i in range(0, len(A_KEY) - 7):
            frag = A_KEY[i:i + 8]
            if frag.startswith("sk-ant-"):
                continue
            assert frag not in everything, f"fragment {frag!r} leaked"
        assert len(ad.calls) >= 4, "the scenario really exercised the failing provider"
    finally:
        e.close()


def test_sdk_loggers_are_pinned_to_warning(env):
    for name in ("anthropic", "openai", "httpx", "httpx2", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


def test_live_server_smoke_script(tmp_path):
    """tools/live_smoke.py starts the real uvicorn server on 127.0.0.1 and walks through the keyless flow."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([sys.executable, str(root / "tools" / "live_smoke.py")], capture_output=True, text=True, timeout=120, cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "all checks passed" in p.stdout and "FAIL" not in p.stdout
