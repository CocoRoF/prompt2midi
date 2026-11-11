"""Start the real server (python -m prompt2midi.api, uvicorn with proxy_headers=False) on a free port and walk through the keyless flow over HTTP.

    python tools/live_smoke.py            # prints what happened; exit code 0 = all checks passed

Uses a temporary data directory, no API keys, no outside network (127.0.0.1 only).
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port, tmp = free_port(), tempfile.mkdtemp(prefix="p2m-live-")
    env = dict(os.environ, P2M_DATA_DIR=os.path.join(tmp, "data"), PYTHONPATH=os.path.join(ROOT, "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    for k in ("P2M_SECRET_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(k, None)
    log = open(os.path.join(tmp, "server.log"), "w+")
    proc = subprocess.Popen([sys.executable, "-m", "prompt2midi.api", "--host", "127.0.0.1", "--port", str(port)], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    base = f"http://127.0.0.1:{port}"
    ok = True
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def check(label, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'ok' if cond else 'FAIL'}] {label} {extra}")

    try:
        for _ in range(100):
            try:
                call("GET", "/healthz")
                break
            except Exception:
                time.sleep(0.1)
        s, _h, b = call("GET", "/healthz"); check("GET /healthz", s == 200, b.decode())
        s, _h, b = call("GET", "/api/providers"); check("GET /api/providers (only rule available)", s == 200 and [p["available"] for p in json.loads(b)] == [True, False, False], b.decode())
        s, _h, b = call("POST", "/api/generate", {"prompt": "A calm piano piece in C major, 70 BPM"})
        j = json.loads(b)
        check("POST /api/generate without any key", s == 200 and j["provider_used"] == "rule", f"provider_used={j.get('provider_used')} seed={j.get('seed')} tempo={j['spec']['tempo_bpm']} key={j['spec']['key']}")
        s, h, mid = call("GET", j["midi_url"])
        check("GET midi_url", s == 200 and mid[:4] == b"MThd" and h.get("content-type") == "audio/midi" and "attachment" in h.get("content-disposition", ""), f"{len(mid)} bytes, {h.get('content-type')}, {h.get('content-disposition')}")
        path = os.path.join(tmp, "out.mid")
        open(path, "wb").write(mid)
        try:
            sys.path.insert(0, os.path.join(ROOT, "src"))
            import mido
            from prompt2midi.validate import check_midi, validate_spec
            spec, _ = validate_spec(j["spec"])
            check("downloaded file re-parses and passes MidiCheck", not check_midi(mid, spec), f"mido: {mido.MidiFile(path).length:.1f}s")
        except ImportError as e:  # pragma: no cover
            print("skip re-parse:", e)
        s, _h, b = call("POST", "/api/generate", {"prompt": "x", "provider": "anthropic"}); check("LLM provider refused while public_llm is off", s == 403 and json.loads(b)["error"]["code"] == "llm_disabled", b.decode())
        s, _h, b = call("POST", "/api/generate", {"prompt": ""}); check("empty prompt -> 400 invalid_input", s == 400 and json.loads(b)["error"]["code"] == "invalid_input")
        s, _h, _b = call("GET", "/"); check("GET / (screen)", s == 200)
        s, _h, _b = call("GET", "/admin"); check("GET /admin (screen)", s == 200)
        s, _h, _b = call("GET", "/README.md"); check("README.md is not served", s == 404)
        s, _h, b = call("GET", "/api/admin/status"); check("admin not configured yet", json.loads(b) == {"configured": False})
        log.seek(0)
        token = re.search(r"설정 토큰[^:]*: (\S+)", log.read())
        check("setup token printed once in the server log", token is not None)
        s, _h, b = call("POST", "/api/admin/setup", {"setup_token": token.group(1), "password": "live smoke password 1"}); check("admin setup", s == 200)
        s, _h, b = call("POST", "/api/admin/setup", {"setup_token": token.group(1), "password": "another password 123"}); check("second setup refused", s == 409)
        s, h, b = call("POST", "/api/admin/login", {"password": "live smoke password 1"}); csrf = json.loads(b).get("csrf_token", "")
        check("admin login", s == 200 and csrf, "cookie: " + "; ".join(p for p in h.get("set-cookie", "").split("; ")[1:]))
        s, _h, b = call("PUT", "/api/admin/limits", {"max_retries": 1}); check("change without CSRF header -> 403", s == 403 and json.loads(b)["error"]["code"] == "csrf_failed")
        s, _h, b = call("PUT", "/api/admin/limits", {"max_retries": 1}, {"X-CSRF-Token": csrf}); check("change with CSRF header", s == 200)
        s, _h, b = call("GET", "/api/admin/settings"); st = json.loads(b); check("settings contain no key material", s == 200 and not st["providers"]["anthropic"]["key_set"], json.dumps(st["storage"]))
        modes = {n: oct(os.stat(os.path.join(tmp, "data", n)).st_mode & 0o777) for n in os.listdir(os.path.join(tmp, "data"))}
        check("data files are 0600", all(m == "0o600" for m in modes.values()), str(modes))
        log.seek(0)
        check("setup token appears exactly once in the log", log.read().count(token.group(1)) == 1)
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("RESULT:", "all checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
