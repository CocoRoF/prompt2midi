"""python -m prompt2midi.evalkit <set.json> --provider rule|fake [--out DIR] [--fallback]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .model import SetError, load_set
from .report import summary_md, write_csv
from .runner import PROVIDERS, run_set


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m prompt2midi.evalkit", description=__doc__)
    ap.add_argument("set", help="평가 세트 JSON 파일 경로 (보류 세트는 경로로만 받는다. 레포에 두지 않는다)")
    ap.add_argument("--provider", choices=[*PROVIDERS, "llm"], required=True)
    ap.add_argument("--out", default="out/evalkit", help="results.csv 와 summary.md 를 쓸 폴더 (기본 out/evalkit)")
    ap.add_argument("--fallback", action="store_true", help="공급자가 실패하면 규칙 공급자로 대체(기본: 끔)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--tempo-tol", type=float, default=None, metavar="RATIO",
                    help="템포 허용오차를 상대 비율로(예: 0.10 = ±10%%). 신경망 공급자 비교용. 기본은 켜지 않음(±0.5 BPM)")
    args = ap.parse_args(argv)
    if args.tempo_tol is not None and not 0 < args.tempo_tol <= 1:
        ap.error("--tempo-tol 은 0 초과 1 이하의 비율이어야 합니다 (예: 0.10)")

    try:
        eval_set, sha = load_set(args.set)
    except (SetError, OSError) as exc:
        print(f"세트를 읽지 못했습니다: {exc}", file=sys.stderr)
        for p in getattr(exc, "problems", [])[:20]:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if args.provider == "llm":
        print("미실행(키 없음): llm 공급자는 이 도구에서 실행하지 않습니다. 키 등록 전에는 호출·비용이 없습니다.")
        return 0
    results = run_set(eval_set, args.provider, fallback=args.fallback, max_retries=args.max_retries, tempo_tol=args.tempo_tol)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(results, out / "results.csv")
    md = summary_md(results, set_name=eval_set.name, set_sha=sha, provider=args.provider, fallback=args.fallback, tempo_tol=args.tempo_tol)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"쓴 파일: {out / 'results.csv'}, {out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
