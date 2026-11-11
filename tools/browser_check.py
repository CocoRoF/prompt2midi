#!/usr/bin/env python3
"""헤드리스 Chromium 으로 공개 화면(/)과 관리자 화면(/admin)을 점검한다 (docs/manual-check.md 의 자동 대체분).

사용:  python tools/browser_check.py [--out out/browser] [--port 8791]
필요:  pip install -r tools/requirements-ci.txt  후  python -m playwright install chromium
동작:  임시 데이터 디렉터리로 `python -m prompt2midi.api` 서버를 띄우고(서버 로그에서 설정 토큰을 읽음), 브라우저로 흐름을 따라 하며
       단언한다. 하나라도 실패하면 종료 코드 1. 스크린샷은 --out 에 저장한다(CI 는 아티팩트로 올림).
외부 네트워크·LLM 호출 없음: Rule 공급자만 쓰고, 가짜 키는 저장만 하며 "연결 테스트"는 확인 창만 보고 취소한다.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import mido
from playwright.sync_api import Page, sync_playwright

# 시험용 값은 소스에 적지 않고 실행할 때마다 무작위로 만든다(비밀번호·키 모양의 리터럴이 레포에 남지 않게).
FAKE_KEY = "x" + secrets.token_hex(16)  # 형식상 어떤 공급자 키도 아님(16~512자, 공백 없음). 서버는 저장만 한다
ADMIN_PASSWORD = secrets.token_hex(12)  # 24자
WRONG_TOKEN = secrets.token_hex(16)
OTHER_PASSWORD = secrets.token_hex(12)
TOKEN_RE = re.compile(r"이 줄은 한 번만 출력됩니다\): (\S+)")

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"ok: {msg}")
    else:
        print(f"FAIL: {msg}", file=sys.stderr)
        failures.append(msg)


# ---- 서버 ----
def start_server(port: int, data_dir: str, log_path: Path) -> tuple[subprocess.Popen, str]:
    env = dict(os.environ, P2M_DATA_DIR=data_dir)
    env.pop("P2M_SECRET_KEY", None)  # 평문 저장 경고 배너 점검을 위해
    env.pop("P2M_LIVE", None)
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "prompt2midi.api", "--host", "127.0.0.1", "--port", str(port), "--data-dir", data_dir],  # 진입점이 proxy_headers=False 로 띄운다
        stdout=log, stderr=subprocess.STDOUT, env=env,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise SystemExit("서버가 40초 안에 뜨지 않음:\n" + log_path.read_text(encoding="utf-8"))
    text = log_path.read_text(encoding="utf-8")
    m = TOKEN_RE.search(text)
    if not m:
        proc.kill()
        raise SystemExit("서버 로그에서 설정 토큰을 찾지 못함")
    return proc, m.group(1)


# ---- 페이지 도우미 ----
class Watch:
    """콘솔 오류·페이지 오류·CSP 위반·실패한 요청을 모은다."""

    def __init__(self, page: Page, label: str, expected_http: frozenset[int] = frozenset()):
        # expected_http: 일부러 일으킨 실패(틀린 토큰 403, 재설정 409, 범위 밖 400, 로그아웃 상태 401)의 /api/admin/ 응답 코드.
        # 브라우저는 4xx 응답마다 콘솔에 "Failed to load resource" 오류를 남기므로 그 코드만 건너뛴다. 그 밖의 오류는 모두 센다.
        self.label, self.problems, self.csp_seen = label, [], []
        self.expected = expected_http
        page.add_init_script(
            "window.__csp=[];document.addEventListener('securitypolicyviolation',function(e){window.__csp.push(e.violatedDirective+' '+e.blockedURI)});"
        )
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: self.problems.append(f"requestfailed: {r.url} {r.failure}"))
        page.on("response", self._response)

    def _console(self, m) -> None:
        if m.type not in ("error", "warning"):
            return
        mm = re.search(r"Failed to load resource: the server responded with a status of (\d+)", m.text)
        if mm and int(mm.group(1)) in self.expected:
            return
        self.problems.append(f"console.{m.type}: {m.text}")

    def _response(self, r) -> None:
        if r.status >= 400 and not (r.status in self.expected and "/api/admin/" in r.url):
            self.problems.append(f"http {r.status}: {r.url}")

    def flush(self, page: Page) -> None:
        """페이지를 다시 불러오기 전에 지금까지의 CSP 위반을 모아 둔다(다시 불러오면 window 값이 사라짐)."""
        self.csp_seen += page.evaluate("window.__csp || []")

    def csp(self, page: Page) -> list[str]:
        return self.csp_seen + page.evaluate("window.__csp || []")

    def report(self, page: Page) -> None:
        csp = self.csp(page)
        check(not self.problems, f"{self.label}: 콘솔 오류·경고·실패한 요청 0건" + (f" (있음: {self.problems})" if self.problems else ""))
        check(not csp, f"{self.label}: CSP 위반 0건" + (f" (있음: {csp})" if csp else ""))


def no_hscroll(page: Page, label: str) -> None:
    w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check(w[0] <= w[1], f"{label}: 가로 스크롤 없음 (scrollWidth {w[0]} <= clientWidth {w[1]})")


def visible(page: Page, sel: str) -> bool:
    return page.locator(sel).is_visible()


CONTRAST_JS = """
() => {
  function parse(c){const m=c.match(/rgba?\\(([^)]+)\\)/); if(!m) return null; const p=m[1].split(',').map(s=>parseFloat(s)); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1};}
  function lum(c){const f=v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)};return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b);}
  function bgOf(el){while(el){const c=parse(getComputedStyle(el).backgroundColor); if(c && c.a>0.99) return c; el=el.parentElement;} return {r:255,g:255,b:255,a:1};}
  const bad=[]; const seen=new Set();
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
    const t=walker.currentNode; if(!t.textContent.trim()) continue;
    const el=t.parentElement; if(!el||seen.has(el)) continue; seen.add(el);
    const cs=getComputedStyle(el); const r=el.getBoundingClientRect();
    if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
    if(el.closest('.skip')) continue; // 포커스 전에는 화면 밖에 있는 링크
    if(el.closest('button:disabled')) continue; // 비활성 컨트롤은 대비 기준 제외(WCAG)
    const fg=parse(cs.color), bg=bgOf(el); if(!fg) continue;
    const L1=lum(fg), L2=lum(bg); const ratio=(Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    const size=parseFloat(cs.fontSize), bold=parseInt(cs.fontWeight)>=700;
    const need=(size>=24||(size>=18.66&&bold))?3:4.5;
    if(ratio<need) bad.push(el.tagName.toLowerCase()+' "'+t.textContent.trim().slice(0,24)+'" '+ratio.toFixed(2)+'<'+need);
  }
  return bad;
}
"""


def contrast(page: Page, label: str) -> None:
    bad = page.evaluate(CONTRAST_JS)
    check(not bad, f"{label}: 글자 색 대비 WCAG AA 기준(계산한 CSS 값 기준) 미달 0건" + (f" (미달: {bad})" if bad else ""))


# ---- 공개 화면 ----
def check_public(browser, base: str, out: Path) -> None:
    ctx = browser.new_context(viewport={"width": 1100, "height": 800}, accept_downloads=True)
    ctx.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))  # 파비콘 404 는 점검 대상이 아님
    page = ctx.new_page()
    watch = Watch(page, "공개 화면 '/'")
    page.goto(base + "/")
    page.wait_for_selector("#provider option", state="attached")
    page.screenshot(path=out / "public-desktop.png", full_page=True)

    # 1: 첫 화면
    check(page.title() == "prompt2midi", "'/' 제목이 prompt2midi")
    opts = page.locator("#provider option").all_text_contents()
    check("Rule (offline)" in opts, f"공급자 목록에 'Rule (offline)' 있음 (목록: {opts})")
    check(opts == ["Rule (offline)"], "키가 없을 때 공급자는 Rule 하나뿐 (LLM 공급자 비노출)")
    check(page.locator("#submit").is_enabled(), "생성 버튼이 켜져 있음")
    contrast(page, "'/' 첫 화면")

    # 5: 빈 프롬프트
    page.click("#submit")
    check(visible(page, "#error"), "빈 프롬프트로 생성하면 오류 문구가 보임")
    check("프롬프트를 입력" in page.locator("#error").inner_text(), "오류 문구: 프롬프트를 입력해 달라는 내용")
    check(page.evaluate("document.activeElement.id") == "prompt", "빈 프롬프트 오류 뒤 포커스가 프롬프트 칸으로 감")
    contrast(page, "'/' 오류 표시")

    # 6: Tab 순서와 포커스 표시
    watch.flush(page)
    page.reload()
    page.wait_for_selector("#provider option", state="attached")
    order, no_outline = [], []
    for _ in range(6):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "(() => {const e=document.activeElement; const cs=getComputedStyle(e);"
            "return {id:e.id||e.className||e.tagName, outline:cs.outlineStyle, w:parseFloat(cs.outlineWidth)};})()"
        )
        order.append(info["id"])
        if info["outline"] == "none" or info["w"] < 2:
            no_outline.append(info["id"])
    expect = ["skip", "prompt", "provider", "seed", "fallback", "submit"]
    check(order == expect, f"Tab 순서: 건너뛰기 링크 → 프롬프트 → 공급자 → 시드 → 체크박스 → 생성 (실제 {order})")
    check(not no_outline, f"Tab 으로 이동한 요소 모두 포커스 윤곽이 보임 (윤곽 없는 요소: {no_outline})")

    # 2·3·4: 생성 → 결과 → 다운로드 → 사양 접기
    page.fill("#prompt", "밝고 느린 피아노 멜로디")
    check(page.locator("#prompt-count").inner_text() == "13자", "글자 수 표시가 입력에 따라 갱신됨")
    page.click("#submit")
    page.wait_for_selector("#result:not(.hidden)", timeout=20000)
    check("rule" in page.locator("#result-meta").inner_text().lower(), "결과 카드에 사용한 공급자(rule)·시드가 표시됨")
    check(page.evaluate("document.activeElement.id") == "result", "생성 뒤 키보드 포커스가 결과 카드로 이동함")
    href = page.locator("#download").get_attribute("href") or ""
    check(href.startswith("/api/midi/") and href.endswith(".mid"), f"MIDI 내려받기 링크가 표시됨 ({href[:20]}…)")
    check(visible(page, "#download"), "내려받기 버튼이 화면에 보임")
    with page.expect_download(timeout=15000) as dl:
        page.click("#download")
    path = dl.value.path()
    data = Path(path).read_bytes()
    check(data[:4] == b"MThd" and len(data) > 30, f"내려받은 파일이 MIDI 헤더(MThd)로 시작함 ({len(data)}바이트)")
    try:
        mf = mido.MidiFile(file=__import__("io").BytesIO(data))
        parsed = sum(len(t) for t in mf.tracks) > 0
    except Exception as e:  # noqa: BLE001
        parsed, mf = False, None
        print(f"MIDI 파싱 오류: {e}", file=sys.stderr)
    check(parsed, f"내려받은 파일이 MIDI 로 해석되고 이벤트가 있음 (트랙 {len(mf.tracks) if mf else 0}개, 길이 {mf.length:.1f}초)" if mf else "내려받은 파일이 MIDI 로 해석됨")
    check(dl.value.suggested_filename.endswith(".mid"), f"내려받은 파일 이름이 .mid ({dl.value.suggested_filename})")
    check(page.locator("details").get_attribute("open") is None, "사양(JSON)은 처음에 접혀 있음")
    page.click("summary")
    check(page.locator("#spec").is_visible() and '"tempo_bpm"' in page.locator("#spec").inner_text(), "사양 펼치면 JSON 이 보임")
    check(page.evaluate("getComputedStyle(document.getElementById('spec')).overflowX") in ("auto", "scroll"), "사양 상자는 가로 스크롤 가능")
    page.screenshot(path=out / "public-result-desktop.png", full_page=True)
    contrast(page, "'/' 결과 화면")
    watch.report(page)

    # 7: 360px 폭
    page.set_viewport_size({"width": 360, "height": 740})
    watch.flush(page)
    page.reload()
    page.wait_for_selector("#provider option", state="attached")
    no_hscroll(page, "'/' 360px 첫 화면")
    page.fill("#prompt", "밝고 느린 피아노 멜로디")
    page.click("#submit")
    page.wait_for_selector("#result:not(.hidden)", timeout=20000)
    page.click("summary")
    no_hscroll(page, "'/' 360px 결과·사양 펼침")
    bw = page.evaluate("(() => {const b=document.getElementById('submit').getBoundingClientRect(), f=document.getElementById('gen-form').getBoundingClientRect(); return b.width/f.width;})()")
    check(bw > 0.9, f"'/' 360px 에서 생성 버튼이 폼 전체 폭 (비율 {bw:.2f})")
    page.screenshot(path=out / "public-result-360.png", full_page=True)
    watch.report(page)  # 재로드·모바일 구간 포함 누적 점검
    ctx.close()


# ---- 관리자 화면 ----
def check_admin(browser, base: str, token: str, out: Path) -> None:
    ctx = browser.new_context(viewport={"width": 1100, "height": 800})
    ctx.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page = ctx.new_page()
    watch = Watch(page, "관리자 화면 '/admin'", frozenset({400, 401, 403, 409}))
    dialogs: list[str] = []

    def on_dialog(d):
        dialogs.append(d.message)
        d.dismiss()  # 연결 테스트는 외부 호출이므로 항상 취소한다

    page.on("dialog", on_dialog)

    page.goto(base + "/admin")
    page.wait_for_selector("#view-setup:not(.hidden)")
    check(visible(page, "#view-setup") and not visible(page, "#view-panel"), "최초 실행: 최초 설정 화면이 보임")
    page.screenshot(path=out / "admin-setup.png", full_page=True)
    contrast(page, "'/admin' 최초 설정")

    # 360px 에서도 설정 화면
    page.set_viewport_size({"width": 360, "height": 740})
    no_hscroll(page, "'/admin' 360px 최초 설정")
    page.set_viewport_size({"width": 1100, "height": 800})

    # 틀린 토큰
    page.fill("#setup-token", WRONG_TOKEN)
    page.fill("#setup-pass", ADMIN_PASSWORD)
    page.fill("#setup-pass2", ADMIN_PASSWORD)
    page.click("#setup-form button[type=submit]")
    page.wait_for_selector("#error:not(.hidden)")
    check(visible(page, "#view-setup"), "틀린 설정 토큰이면 설정 화면에 머무르고 오류가 보임")
    # 비밀번호 불일치
    page.fill("#setup-token", token)
    page.fill("#setup-pass2", ADMIN_PASSWORD + "x")
    page.click("#setup-form button[type=submit]")
    check("일치하지 않" in page.locator("#error").inner_text(), "비밀번호 확인이 다르면 오류 문구가 보임")
    # 정상 설정
    page.fill("#setup-pass2", ADMIN_PASSWORD)
    page.click("#setup-form button[type=submit]")
    page.wait_for_selector("#view-login:not(.hidden)")
    check("설정이 끝났" in page.locator("#msg").inner_text(), "설정 완료 안내가 보이고 로그인 화면으로 넘어감")
    check(page.evaluate("document.activeElement.id") == "login-pass", "로그인 화면에서 비밀번호 칸에 포커스")
    # 같은 토큰 재사용(1회용): API 로 직접 확인
    reuse = page.evaluate(
        "([t, pw]) => fetch('/api/admin/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({setup_token:t,password:pw})}).then(r=>r.text().then(()=>r.status))",
        [token, OTHER_PASSWORD],
    )
    check(reuse == 409, f"이미 설정된 뒤 같은 토큰으로 다시 설정하면 409 (실제 {reuse})")

    # 로그인
    page.fill("#login-pass", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#view-panel:not(.hidden)")
    check(visible(page, "#banner-plain"), "P2M_SECRET_KEY 가 없으면 '암호화되지 않고 저장' 배너가 보임")
    check(page.locator("#providers > section.card").count() == 2, "공급자 카드가 2개(Anthropic·OpenAI)")
    check(visible(page, "#public-form") and visible(page, "#limits-form"), "공개 설정·제한값 카드가 보임")
    check(page.locator("#public-llm").is_checked() is False, "public_llm 기본값은 꺼짐")
    page.screenshot(path=out / "admin-panel.png", full_page=True)
    contrast(page, "'/admin' 관리 패널")

    # 9: 가짜 키 저장 → 입력칸 비워짐, 끝 4자만
    page.fill("#key-anthropic", FAKE_KEY)
    page.fill("#model-anthropic", "ci-model")
    page.locator("#providers > section.card").first.get_by_role("button", name="저장").click()
    page.wait_for_selector("text=키 등록됨")
    check(page.input_value("#key-anthropic") == "", "키 저장 뒤 입력칸이 비워짐")
    hint = "…" + FAKE_KEY[-4:]
    check(hint in page.locator("#providers").inner_text(), "저장 뒤 끝 4자 힌트(…+끝 4자)만 표시됨")
    watch.flush(page)
    page.reload()
    page.wait_for_selector("#view-panel:not(.hidden)")
    html = page.content()
    check(FAKE_KEY not in html and FAKE_KEY[:-4] not in html, "새로 고침 뒤에도 세션이 유지되고 DOM 어디에도 키 전체가 없음")
    check(FAKE_KEY not in page.evaluate("JSON.stringify([localStorage, sessionStorage])"), "브라우저 저장소에도 키가 없음")
    api = page.evaluate("fetch('/api/admin/settings').then(r=>r.text())")
    check(FAKE_KEY not in api and FAKE_KEY[:-4] not in api, "설정 API 응답에도 키 전체가 없음")

    # 10: 연결 테스트는 확인 창(비용 안내)만 보고 취소 — 외부 호출 없음
    page.locator("#providers > section.card").first.get_by_role("button", name="연결 테스트").click()
    check(len(dialogs) == 1 and "비용" in dialogs[0], f"연결 테스트를 누르면 비용 안내 확인 창이 뜸 ({dialogs})")
    check("테스트 중" not in page.locator("#providers").inner_text(), "확인 창을 취소하면 테스트 요청이 시작되지 않음")

    # 공개 설정·제한값 저장
    page.fill("#lim-rpm", "7")
    page.locator("#limits-form").get_by_role("button", name="저장").click()
    page.wait_for_selector("text=제한값을 저장했습니다")
    check(page.input_value("#lim-rpm") == "7", "제한값 저장 뒤 값이 유지됨")
    page.fill("#lim-chars", "999999")
    page.locator("#limits-form").get_by_role("button", name="저장").click()
    page.wait_for_selector("#error:not(.hidden)")
    check(visible(page, "#error"), "범위 밖 제한값은 오류 표시로 거절됨")

    # 7: 360px 관리 패널
    page.set_viewport_size({"width": 360, "height": 740})
    watch.flush(page)
    page.reload()
    page.wait_for_selector("#view-panel:not(.hidden)")
    no_hscroll(page, "'/admin' 360px 관리 패널")
    page.screenshot(path=out / "admin-panel-360.png", full_page=True)

    # 로그아웃 → 로그인 화면, 다시 열면 로그인 필요
    page.click("#logout")
    page.wait_for_selector("#view-login:not(.hidden)")
    watch.flush(page)
    page.reload()
    page.wait_for_selector("#view-login:not(.hidden)")
    check(not visible(page, "#view-panel"), "로그아웃 뒤 다시 열면 패널이 보이지 않고 로그인 화면")
    watch.report(page)
    ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/browser")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--base-url", help="이미 떠 있는 서버를 쓰려면(설정 토큰은 --token 으로)")
    ap.add_argument("--token")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    proc = None
    tmp = tempfile.TemporaryDirectory()
    try:
        if a.base_url:
            base, token = a.base_url.rstrip("/"), a.token or ""
        else:
            proc, token = start_server(a.port, os.path.join(tmp.name, "data"), out / "server.log")
            base = f"http://127.0.0.1:{a.port}"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            print(f"Chromium {browser.version}")
            check_public(browser, base, out)
            check_admin(browser, base, token, out)
            browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        tmp.cleanup()

    log = (out / "server.log")
    if log.exists():
        # 서버 로그 첨부 전에 설정 토큰 줄은 가린다(1회용이지만 습관상)
        log.write_text(TOKEN_RE.sub("이 줄은 한 번만 출력됩니다): <가림>", log.read_text(encoding="utf-8")), encoding="utf-8")
    if failures:
        print(f"\n실패 {len(failures)}건:", file=sys.stderr)
        for f in failures:
            print(f" - {f}", file=sys.stderr)
        return 1
    print("\n브라우저 점검 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
