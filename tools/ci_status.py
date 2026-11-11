#!/usr/bin/env python3
"""GitHub 공개 API(인증 불필요)로 한 커밋의 check-run(Actions job 별) 결과를 읽어 docs/ci-status.md 를 만든다.

사용: python tools/ci_status.py [커밋 해시] [--repo owner/name] [--out docs/ci-status.md]
커밋을 생략하면 origin/main 의 최신 커밋. 자동 갱신이 아니라 실행한 시점의 스냅샷이다.
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100"


def fetch(repo: str, sha: str) -> dict:
    req = urllib.request.Request(API.format(repo=repo, sha=sha), headers={"Accept": "application/vnd.github+json", "User-Agent": "prompt2midi-ci-status"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def render(repo: str, sha: str, data: dict, now: str) -> str:
    rows = sorted(data.get("check_runs", []), key=lambda r: r["name"])
    lines = [
        "# CI 상태 스냅샷",
        "",
        f"- 저장소: `{repo}`",
        f"- 커밋: `{sha}`",
        f"- 조회 시각: {now} (UTC)",
        f"- 출처: GitHub 공개 API `GET /repos/{repo}/commits/<sha>/check-runs` (인증 없이 조회)",
        "- **자동으로 갱신되지 않는 스냅샷이다.** 최신 상태는 Actions 탭이나 위 API 로 다시 확인한다. 갱신: `python tools/ci_status.py <커밋>`",
        "",
        "| job | 상태 | 결과 | 시작 (UTC) | 끝 (UTC) | 기록 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['name']} | {r['status']} | {r.get('conclusion') or '-'} | {r.get('started_at') or '-'} | {r.get('completed_at') or '-'} | [보기]({r['html_url']}) |")
    if not rows:
        lines.append("| (check-run 없음) | | | | | |")
    lines += ["", f"합계 {data.get('total_count', len(rows))}개 job."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sha", nargs="?")
    ap.add_argument("--repo", default="CocoRoF/prompt2midi")
    ap.add_argument("--out", default="docs/ci-status.md")
    a = ap.parse_args()
    sha = a.sha or subprocess.run(["git", "rev-parse", "origin/main"], check=True, capture_output=True, text=True).stdout.strip()
    data = fetch(a.repo, sha)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(a.repo, sha, data, now))
    print(f"{a.out} 작성: {sha[:7]} job {len(data.get('check_runs', []))}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
