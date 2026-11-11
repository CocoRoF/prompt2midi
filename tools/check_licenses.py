#!/usr/bin/env python3
"""pyproject.toml 의 의존성이 data/sources.csv 에 모두 기록됐는지 검사한다. 빠진 게 있으면 종료 코드 1.

사용: python tools/check_licenses.py
대상: [project].dependencies, [project.optional-dependencies] 의 직접 의존성, 그리고 CI 전용 tools/requirements-ci.txt 의 직접 의존성.
sources.csv 의 name 열은 "패키지 버전" 형식(예: "pydantic 2.13.5")이며 패키지 이름만 비교한다(대소문자·-/_ 무시).
"""
import csv
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def req_name(req: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)", req)
    if not m:
        raise ValueError(f"의존성 문자열을 읽을 수 없음: {req!r}")
    return norm(m.group(1))


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs += extra
    ci_file = ROOT / "tools" / "requirements-ci.txt"
    if ci_file.exists():
        for line in ci_file.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                reqs.append(line)
    wanted = {req_name(r) for r in reqs}

    recorded = set()
    with open(ROOT / "data" / "sources.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["type"].startswith("python dependency"):
                recorded.add(norm(row["name"].split()[0]))

    missing = sorted(wanted - recorded)
    if missing:
        print("data/sources.csv 에 라이선스 기록이 없는 의존성: " + ", ".join(missing))
        print("행을 추가하세요 (type=python dependency …, license, commercial_use, as_of, read 열 포함).")
        return 1
    print(f"의존성 {len(wanted)}개 모두 data/sources.csv 에 기록됨: {', '.join(sorted(wanted))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
