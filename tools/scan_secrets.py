#!/usr/bin/env python3
"""레포 추적 파일에서 비밀 문자열처럼 보이는 것을 찾는다. 있으면 종료 코드 1.

사용: python tools/scan_secrets.py
오탐 처리: 해당 줄 끝에 `pragma: allowlist secret` 주석을 단다 (예: 시험용 가짜 키).
출력에는 일치한 값 전체를 찍지 않고 앞 6자만 보인다.
"""
import re
import subprocess
import sys

# 패턴을 이어 붙여 만들어 이 파일 자신이 걸리지 않게 한다.
PATTERNS = {
    "Anthropic API key": r"sk-" + r"ant-[A-Za-z0-9_\-]{20,}",
    "OpenAI API key": r"sk-" + r"(?:proj-)?[A-Za-z0-9_\-]{32,}",
    "AWS access key id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "Private key block": r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
}
ALLOW_MARK = "pragma: allowlist secret"


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def main() -> int:
    compiled = {name: re.compile(rx) for name, rx in PATTERNS.items()}
    hits = []
    for path in tracked_files():
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\0" in data[:4096]:  # 이진 파일은 건너뜀
            continue
        for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if ALLOW_MARK in line:
                continue
            for name, rx in compiled.items():
                m = rx.search(line)
                if m:
                    hits.append((path, lineno, name, m.group(0)[:6] + "…"))
                    break  # 한 줄에 한 번만 보고
    for path, lineno, name, shown in hits:
        print(f"{path}:{lineno}: {name} 의심 ({shown})")
    if hits:
        print(f"비밀 문자열 의심 {len(hits)}건. 진짜면 키를 폐기·교체하고, 가짜면 줄 끝에 '# {ALLOW_MARK}' 를 단다.")
        return 1
    print("비밀 문자열 의심 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
