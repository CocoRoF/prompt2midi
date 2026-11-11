"""python -m prompt2midi.api: uvicorn must run with proxy_headers=False; forged X-Forwarded-For must not defeat the login limit."""
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from prompt2midi.api import __main__ as runner
from prompt2midi.api.app import PROXY_WARNING, uvicorn_cli_without_no_proxy_headers
from tests.api_helpers import Env

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "forged header test password"


# ---------------- unit: arguments and uvicorn.run kwargs ----------------

def test_defaults_and_options():
    a = runner.parse_args([])
    assert (a.host, a.port, a.data_dir) == ("127.0.0.1", 8000, None)
    a = runner.parse_args(["--host", "0.0.0.0", "--port", "9001", "--data-dir", "/data", "--log-level", "warning"])
    assert (a.host, a.port, a.data_dir, a.log_level) == ("0.0.0.0", 9001, "/data", "warning")
    with pytest.raises(SystemExit):
        runner.parse_args(["--port", "abc"])


def test_run_kwargs_always_disable_proxy_headers():
    kw = runner.build_run_kwargs(runner.parse_args([]))
    assert kw["proxy_headers"] is False and kw["host"] == "127.0.0.1" and kw["port"] == 8000
    assert "proxy_headers" not in {a for a in dir(runner.parse_args([])) if not a.startswith("_")}, "proxy handling is not a user option"
    with pytest.raises(SystemExit):
        runner.parse_args(["--proxy-headers"])


def test_main_calls_uvicorn_run_with_proxy_headers_false(monkeypatch, tmp_path):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    monkeypatch.delenv("P2M_DATA_DIR", raising=False)
    assert runner.main(["--port", "8123", "--data-dir", str(tmp_path / "d")]) == 0
    app, kw = calls[0]
    assert kw["proxy_headers"] is False and kw["port"] == 8123
    assert app.state.p2m.cfg.data_dir == tmp_path / "d" and (tmp_path / "d").is_dir()
    assert os.environ["P2M_DATA_DIR"] == str(tmp_path / "d")
    monkeypatch.delenv("P2M_DATA_DIR")


def test_entry_points_use_the_runner():
    assert 'CMD ["python", "-m", "prompt2midi.api"' in (ROOT / "Dockerfile").read_text()
    smoke = (ROOT / "tools" / "live_smoke.py").read_text()
    assert '"-m", "prompt2midi.api"' in smoke and '"-m", "uvicorn"' not in smoke
    readme = (ROOT / "README.md").read_text()
    assert "python -m prompt2midi.api" in readme and "--no-proxy-headers` 가 필수" in readme
    assert "python -m prompt2midi.api" in (ROOT / "docs" / "api-and-admin.md").read_text()


# ---------------- warning heuristics ----------------

@pytest.mark.parametrize("argv,expected", [
    (["/usr/bin/uvicorn", "prompt2midi.api.app:app"], True),
    (["uvicorn", "prompt2midi.api.app:app", "--no-proxy-headers"], False),
    (["/venv/lib/python3.12/site-packages/uvicorn/__main__.py", "prompt2midi.api.app:app"], True),
    (["/venv/lib/python3.12/site-packages/uvicorn/__main__.py", "x", "--no-proxy-headers"], False),
    (["/x/prompt2midi/api/__main__.py", "--port", "1"], False),
    (["pytest"], False), ([], False),
])
def test_cli_detection(argv, expected):
    assert uvicorn_cli_without_no_proxy_headers(argv) is expected


def test_startup_warning_when_started_by_plain_uvicorn(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "prompt2midi.api.app:app"])
    with caplog.at_level(logging.WARNING, logger="prompt2midi.api"):
        e = Env(tmp_path)
        e.close()
    assert "without --no-proxy-headers" in caplog.text


def test_no_startup_warning_with_the_runner_or_trusted_proxy(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "prompt2midi.api.app:app"])
    with caplog.at_level(logging.WARNING, logger="prompt2midi.api"):
        Env(tmp_path / "a", trust_proxy=True).close()
        monkeypatch.setattr(sys, "argv", ["python", "-m", "prompt2midi.api"])
        Env(tmp_path / "b").close()
    assert "proxy-header" not in caplog.text and "proxy_headers" not in caplog.text and "--no-proxy-headers" not in caplog.text


def test_request_time_warning_when_uvicorn_rewrote_the_client_address(tmp_path, caplog):
    """Simulate uvicorn's default ProxyHeadersMiddleware in front of the app, then send a forged header."""
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    e = Env(tmp_path)
    try:
        wrapped = ProxyHeadersMiddleware(e.app, trusted_hosts="*")
        with TestClient(wrapped) as c, caplog.at_level(logging.WARNING, logger="prompt2midi.api"):
            c.get("/healthz")
            assert "seems to be ON" not in caplog.text, "no header, nothing to notice"
            c.get("/healthz", headers={"X-Forwarded-For": "9.9.9.9"})
            c.get("/healthz", headers={"X-Forwarded-For": "8.8.8.8"})
        assert caplog.text.count("seems to be ON") == 1, "warned once per process"
    finally:
        e.close()


def test_no_request_warning_when_the_header_is_just_ignored(tmp_path, caplog):
    e = Env(tmp_path)
    try:
        with caplog.at_level(logging.WARNING, logger="prompt2midi.api"):
            e.client.get("/healthz", headers={"X-Forwarded-For": "9.9.9.9"})
        assert "seems to be ON" not in caplog.text
    finally:
        e.close()
    t = Env(tmp_path / "t", trust_proxy=True)
    try:
        from fastapi.testclient import TestClient
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        with TestClient(ProxyHeadersMiddleware(t.app, trusted_hosts="*")) as c, caplog.at_level(logging.WARNING, logger="prompt2midi.api"):
            c.get("/healthz", headers={"X-Forwarded-For": "9.9.9.9"})
        assert "seems to be ON" not in caplog.text, "with P2M_TRUST_PROXY=1 the header is meant to be used"
    finally:
        t.close()


# ---------------- real servers ----------------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp, args, env_extra=None, module=None):
        self.port = _free_port()
        self.log = open(Path(tmp) / "server.log", "w+")
        env = dict(os.environ, P2M_DATA_DIR=str(Path(tmp) / "data"), PYTHONPATH=str(ROOT / "src"))
        for k in ("P2M_TRUST_PROXY", "P2M_SECRET_KEY"):
            env.pop(k, None)
        env.update(env_extra or {})
        cmd = module or [sys.executable, "-m", "prompt2midi.api", "--port", str(self.port)]
        self.proc = subprocess.Popen(cmd + args(self.port) if callable(args) else cmd + args, env=env, stdout=self.log, stderr=subprocess.STDOUT, cwd=ROOT)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(150):
            try:
                urllib.request.urlopen(self.base + "/healthz", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start: " + self.logtext())

    def logtext(self):
        self.log.seek(0)
        return self.log.read()

    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read()

    def setup(self):
        import re
        token = re.search(r"설정 토큰[^:]*: (\S+)", self.logtext()).group(1)
        assert self.call("POST", "/api/admin/setup", {"setup_token": token, "password": PASSWORD})[0] == 200

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def forged_attempts(srv, n=7):
    """n wrong-password logins, each claiming a different client address in X-Forwarded-For."""
    return [srv.call("POST", "/api/admin/login", {"password": "wrong password here"}, {"X-Forwarded-For": f"203.0.113.{i}"})[0] for i in range(1, n + 1)]


def test_runner_blocks_forged_xff_from_defeating_the_login_limit(tmp_path):
    srv = Server(tmp_path, [])
    try:
        srv.setup()
        codes = forged_attempts(srv, 7)
        assert codes == [401] * 5 + [429] * 2, codes
        right = srv.call("POST", "/api/admin/login", {"password": PASSWORD}, {"X-Forwarded-For": "198.51.100.1"})[0]
        assert right == 429, "even the right password is refused while locked out, whatever header is sent"
    finally:
        srv.stop()


def test_plain_uvicorn_without_the_flag_is_vulnerable_but_warns(tmp_path):
    """Documents WHY the runner exists: default uvicorn lets forged headers walk around the limit (and the app says so)."""
    srv = Server(tmp_path, lambda port: ["prompt2midi.api.app:app", "--port", str(port), "--host", "127.0.0.1"],
                 module=[sys.executable, "-m", "uvicorn"])
    try:
        srv.setup()
        codes = forged_attempts(srv, 7)
        assert codes == [401] * 7, f"expected the bypass to work on default uvicorn: {codes}"
        log = srv.logtext()
        assert "without --no-proxy-headers" in log and "seems to be ON" in log
    finally:
        srv.stop()


def test_plain_uvicorn_with_the_flag_is_safe(tmp_path):
    srv = Server(tmp_path, lambda port: ["prompt2midi.api.app:app", "--port", str(port), "--host", "127.0.0.1", "--no-proxy-headers"],
                 module=[sys.executable, "-m", "uvicorn"])
    try:
        srv.setup()
        assert forged_attempts(srv, 7) == [401] * 5 + [429] * 2
        assert "seems to be ON" not in srv.logtext() and "without --no-proxy-headers" not in srv.logtext()
    finally:
        srv.stop()


def test_trusted_proxy_mode_uses_the_header_and_only_then(tmp_path):
    srv = Server(tmp_path, [], env_extra={"P2M_TRUST_PROXY": "1"})
    try:
        srv.setup()
        assert forged_attempts(srv, 7) == [401] * 7, "with P2M_TRUST_PROXY=1 each forwarded address has its own counter"
        # the same forwarded address is still limited
        same = [srv.call("POST", "/api/admin/login", {"password": "wrong password here"}, {"X-Forwarded-For": "192.0.2.77"})[0] for _ in range(6)]
        assert same == [401] * 5 + [429]
        assert "seems to be ON" not in srv.logtext()
    finally:
        srv.stop()


def test_runner_data_dir_option_is_used(tmp_path):
    d = tmp_path / "custom-data"
    srv = Server(tmp_path, ["--data-dir", str(d)])
    try:
        srv.setup()
        assert (d / "admin.json").is_file() and not (tmp_path / "data" / "admin.json").exists()
    finally:
        srv.stop()
