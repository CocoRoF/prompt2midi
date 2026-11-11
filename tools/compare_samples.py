#!/usr/bin/env python3
"""전후 비교(engine=v1 / engine=v2): 같은 사양·같은 seed 로 두 MIDI 를 만들고 evalkit 의 proxy_* 지표(M1~M7)를 나란히 놓는다.

    python tools/compare_samples.py [--set SET.json] [--out out/compare-samples] [--ids r01,r02] [--commit HASH]

동작: 세트의 프롬프트마다 현재 RuleProvider 로 사양을 만든다(폴백 없음). 그 사양을 engine=v1 과 engine=v2 로만 바꿔
**같은 seed** 로 컴파일한다. 출력 폴더에 쌍(`<id>.v1.mid`, `<id>.v2.mid`), 비교표(`compare.md`, `compare.csv`)를 쓴다.
키·네트워크·LLM·보류 세트·외부 데이터·text2midi 출력물 없음. 시각·경로를 쓰지 않으므로 같은 입력·같은 커밋이면 파일이 바이트 동일.

**이 표의 지표 변화는 음악이 좋아졌다는 뜻이 아니다.** 범위는 임시값이고 청취는 하지 않았다(청취 확인: tools/listening_pack.py 로 묶음을 만들어 사람이 한다).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from prompt2midi.compiler import CompileError, compile_spec  # noqa: E402
from prompt2midi.evalkit import SetError, load_set  # noqa: E402
from prompt2midi.evalkit import proxy as P  # noqa: E402
from prompt2midi.evalkit.metrics import Context, midicheck_valid, note_integrity  # noqa: E402
from prompt2midi.evalkit.version import EVALKIT_VERSION  # noqa: E402
from prompt2midi.pipeline import GenerationError, generate  # noqa: E402
from prompt2midi.providers import RuleProvider  # noqa: E402
from prompt2midi.spec import effective_seed  # noqa: E402

DEFAULT_SET = ROOT / "data" / "samples" / "baseline-v0" / "set.json"
ENGINES = ("v1", "v2")
HOLDOUT_WORDS = ("holdout", "보류")
SHORT = {P.IN_RANGE: "in", P.OUT_OF_RANGE: "out", P.NA: "na"}

# 비교표의 "대표 값": docs/baseline-expressiveness-v0.md 2절과 같은 선택. 전체 값은 CSV 에 모두 있다.
REPRESENTATIVE = {
    "m1": ("rep_rate", "exact_rate", "var_rate"),
    "m2": ("step_share", "leap_share", "recovery"),
    "m3": ("diversity", "adjacent_same"),
    "m4": ("final_arrived", "section_end_stable"),
    "m5": ("d_dens", "d_reg"),
    "m6": ("chord_move_pc", "voicing_move"),
    "m7": ("off_all",),
}

CAVEAT = (
    "> **읽기 전에.** 이 표의 범위(`in_range`/`out_of_range`)는 **임시 기준**이다(근거·보정 없음). "
    "**지표 값이 바뀐 것은 음악이 좋아졌다는 뜻이 아니다.** 이 비교는 **청취를 하지 않았다(청취 미확인)**. "
    "지표는 규칙 변경 전후의 회귀 탐지와 비교에만 쓰고, 품질의 근거나 최적화 목표로 쓰지 않는다. "
    "`na` 는 측정 불가(유효성 실패·마디 수 부족 등)이다."
)


def git_commit() -> str:
    """HEAD 해시. src/ 나 tools/ 에 커밋하지 않은 변경이 있으면 `-dirty` 를 붙인다. git 이 없으면 `unknown`."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "tools"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fmt(v) -> str:
    return "—" if v is None else str(v)


def pair_cell(a: str, b: str) -> str:
    return a if a == b else f"{a}→{b}"


def score(midi: bytes, spec, expect) -> dict:
    """evalkit.run_set 과 같은 절차: 유효성(midicheck_valid·note_integrity)에 실패한 곡은 모든 지표가 na."""
    base = Context(expect, spec, midi, "")
    valid = midicheck_valid(base).status != "fail" and note_integrity(base).status != "fail"
    return P.evaluate(midi, spec, expect, valid)


def build(items, commit: str):
    """프롬프트마다 (id, lang, tier, seed, {v1: midi, v2: midi}, {v1: proxy, v2: proxy}, error). 실패는 error 에 문장으로."""
    out = []
    for it in items:
        rec = {"id": it.id, "lang": it.lang, "tier": it.tier, "seed": None, "midi": {}, "proxy": {}, "error": ""}
        try:
            res = generate(it.text, provider=RuleProvider(), fallback=False)
        except (GenerationError, ValueError) as exc:
            rec["error"] = str(exc)[:200]
            out.append(rec)
            continue
        seed = effective_seed(res.spec)  # engine 은 해시에 들어가지 않으므로 두 엔진이 같은 seed 를 쓴다
        rec["seed"] = seed
        try:
            for eng in ENGINES:
                spec = res.spec.model_copy(update={"engine": eng})
                midi = compile_spec(spec, seed=seed)
                rec["midi"][eng] = midi
                rec["proxy"][eng] = score(midi, spec, it.expect)
        except CompileError as exc:
            rec["error"] = f"compile: {str(exc)[:160]}"
            rec["midi"], rec["proxy"] = {}, {}
        out.append(rec)
    return out


def csv_text(recs) -> str:
    names = [(k, n) for k, _t, _f, vs in P.PROXIES for n in vs] + list(P.EXTRA_VALUES)
    head = ["id", "lang", "tier", "seed", "same_midi", "v1_sha8", "v2_sha8", "v1_bytes", "v2_bytes", "error"]
    for k, *_ in P.PROXIES:
        head += [f"proxy_{k}_v1", f"proxy_{k}_v2"]
    for k, n in names:
        head += [f"proxy_{k}_{n}_v1", f"proxy_{k}_{n}_v2"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    for r in recs:
        m, p = r["midi"], r["proxy"]
        row = [r["id"], r["lang"], r["tier"], "" if r["seed"] is None else r["seed"],
               (m["v1"] == m["v2"]) if m else "", *[hashlib.sha256(m[e]).hexdigest()[:8] if m else "" for e in ENGINES],
               *[len(m[e]) if m else "" for e in ENGINES], r["error"]]
        for k, *_ in P.PROXIES:
            row += [p[e][k].status if p else "" for e in ENGINES]
        for k, n in names:
            for e in ENGINES:
                v = p[e][k].values.get(n) if p else None
                row.append("" if v is None else v)
        w.writerow(row)
    return buf.getvalue()


def md_text(recs, *, set_name: str, set_sha: str, commit: str, ids_opt: str) -> str:
    ok = [r for r in recs if r["midi"]]
    same = sum(1 for r in ok if r["midi"]["v1"] == r["midi"]["v2"])
    keys = [k for k, *_ in P.PROXIES]
    titles = {k: t for k, t, *_ in P.PROXIES}
    L = [
        "# 전후 비교 — engine v1 / v2", "", CAVEAT, "",
        "## 기록", "",
        f"- 생성 커밋: `{commit}` · `evalkit_version` {EVALKIT_VERSION}",
        f"- 세트: `{set_name}` (파일 sha256 앞 8자 `{set_sha[:8]}`, 프롬프트 {len(recs)}개)",
        f"- 도구 옵션: `--ids` {ids_opt} · 공급자 `rule`(폴백 없음) · 엔진 v1 / v2 · seed = 사양의 유효 seed(두 엔진 동일) · 시각·경로는 기록하지 않음",
        f"- 비교한 쌍 {len(ok)}개 · 두 MIDI 가 바이트 동일한 쌍 {same}개 (동일하면 v2 가 그 곡에서 아무것도 바꾸지 않은 것)"
        + (f" · 생성·컴파일 실패 {len(recs) - len(ok)}개 (`error` 열)" if len(ok) != len(recs) else ""), "",
        "## 1. 지표별 판정 개수 (in_range / out_of_range / na)", "",
        "| 지표 | v1 | v2 |", "|---|---|---|",
    ]
    for k in keys:
        cells = []
        for e in ENGINES:
            st = [r["proxy"][e][k].status for r in ok]
            cells.append(f"{st.count(P.IN_RANGE)} / {st.count(P.OUT_OF_RANGE)} / {st.count(P.NA)}")
        L.append(f"| {titles[k]} | {cells[0]} | {cells[1]} |")
    L += ["", "## 2. 곡별 판정 (v1→v2, 같으면 하나만)", "", "| id | " + " | ".join(k.upper() for k in keys) + " | 같은 MIDI |", "|---|" + "---|" * (len(keys) + 1)]
    for r in recs:
        if not r["midi"]:
            L.append(f"| `{r['id']}` | " + " | ".join("—" for _ in keys) + f" | 실패: {r['error']} |")
            continue
        cells = [pair_cell(SHORT[r["proxy"]["v1"][k].status], SHORT[r["proxy"]["v2"][k].status]) for k in keys]
        L.append(f"| `{r['id']}` | " + " | ".join(cells) + f" | {'예' if r['midi']['v1'] == r['midi']['v2'] else '아니오'} |")
    L += ["", "## 3. 대표 값 (v1 → v2)", "",
          "값 이름은 `proxy_<지표>_<값>` 열과 같다(전체 값은 `compare.csv`). `—` 는 측정 불가. 값이 크거나 작은 것이 좋고 나쁜 음악을 뜻하지 않는다.", ""]
    cols = [(k, n) for k in keys for n in REPRESENTATIVE[k]]
    L += ["| id | " + " | ".join(f"{k.upper()} {n}" for k, n in cols) + " |", "|---|" + "---|" * len(cols)]
    for r in ok:
        cells = []
        for k, n in cols:
            a, b = (r["proxy"][e][k].values.get(n) for e in ENGINES)
            cells.append(fmt(a) if a == b else f"{fmt(a)} → {fmt(b)}")
        L.append(f"| `{r['id']}` | " + " | ".join(cells) + " |")
    L += ["", "## 4. 이 비교가 보여 주지 못하는 것", "",
          "- 음악적으로 나아졌는지(청취 미확인). 사람이 듣는 평가는 `tools/listening_pack.py` 로 블라인드 묶음을 만들어 한다.",
          "- 임시 범위의 타당성(청취 상관 검증 전). 범위를 바꾸면 같은 MIDI 의 판정이 바뀐다.",
          "- 드럼·베이스·화음 트랙의 변화(지표는 선율 중심, M5·M6 만 다른 트랙을 본다).", ""]
    return "\n".join(L)


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=str(DEFAULT_SET), help="평가 세트 JSON(evalkit 형식). 기본 data/samples/baseline-v0/set.json. 보류 세트는 받지 않는다")
    ap.add_argument("--out", default="out/compare-samples", help="출력 폴더(기본 out/compare-samples, 커밋하지 않는다)")
    ap.add_argument("--ids", default=None, help="쉼표로 구분한 프롬프트 id 만 비교(기본 전체)")
    ap.add_argument("--commit", default=None, help="기록할 커밋 해시(기본 git HEAD)")
    args = ap.parse_args(argv)

    try:
        eval_set, sha = load_set(args.set)
    except (SetError, OSError) as exc:
        print(f"세트를 읽지 못했습니다: {exc}", file=out)
        return 2
    if any(w in (str(args.set) + " " + eval_set.name).lower() for w in HOLDOUT_WORDS):
        print("오류: 보류(holdout) 세트로 보입니다(이름·경로). 개발 1팀은 보류 세트를 열지 않습니다.", file=out)
        return 2
    items = eval_set.prompts
    if args.ids:
        want = [i.strip() for i in args.ids.split(",") if i.strip()]
        have = {p.id: p for p in items}
        missing = [i for i in want if i not in have]
        if missing:
            print(f"오류: 세트에 없는 id: {', '.join(missing)}", file=out)
            return 2
        items = [have[i] for i in dict.fromkeys(want)]
    commit = args.commit or git_commit()
    recs = build(items, commit)

    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    for r in recs:
        for eng, midi in r["midi"].items():
            (d / f"{r['id']}.{eng}.mid").write_bytes(midi)
    (d / "compare.csv").write_text(csv_text(recs), encoding="utf-8", newline="")
    (d / "compare.md").write_text(md_text(recs, set_name=eval_set.name, set_sha=sha, commit=commit, ids_opt=args.ids or "전체"), encoding="utf-8")
    ok = sum(1 for r in recs if r["midi"])
    print(f"{ok}/{len(recs)} 쌍을 {d} 에 썼습니다 (compare.md, compare.csv, <id>.v1.mid, <id>.v2.mid). 지표 변화는 음악 개선이 아니며 청취는 하지 않았습니다.", file=out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
