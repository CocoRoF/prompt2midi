#!/usr/bin/env python3
"""청취 A/B 블라인드 묶음: v1/v2 MIDI 쌍 폴더 → 평가자에게 줄 폴더와 정답표(key.csv)를 **분리해** 만든다.

    python tools/listening_pack.py --pairs out/compare-samples --seed 7 --out out/listening-pack

입력  `--pairs`: `<id>.v1.mid` 와 `<id>.v2.mid` 가 쌍으로 들어 있는 폴더(tools/compare_samples.py 의 출력).
출력  `<out>/for_raters/`  평가자에게 주는 폴더: 곡마다 `p01/A.mid`·`p01/B.mid`, `score_sheet.csv`, `README.md`.
      `<out>/answer_key/`  **평가자에게 주지 않는다**: `key.csv`(어느 쪽이 v1/v2), `excluded.csv`, `pack_info.json`, `README.md`.
배정은 시드로 결정된다(같은 입력·같은 시드 → 바이트 동일). 곡 순서와 A/B 는 해시로 섞이며 A 가 v1 인 곡과 v2 인 곡이 반씩(홀수면 한 곡 차이)이다.
평가자 폴더에는 `v1`·`v2`·engine 이라는 이름이 파일명·폴더명·문서 어디에도 나오지 않는다. MIDI 를 오디오로 렌더하지 않는다(사운드폰트는 법무 확인 대기).
키·네트워크·LLM·보류 세트·외부 데이터 없음.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

ENGINES = ("v1", "v2")
PAIR_RE = re.compile(r"^(?P<id>[A-Za-z0-9_.-]{1,64})\.(?P<eng>v1|v2)\.mid$")
RATER_DIR, KEY_DIR = "for_raters", "answer_key"

SCORE_HEADER = ["곡", "선호(A/B/동일)", "메모"]

RATER_README = """# 청취 평가 안내

곡마다 두 개의 MIDI 파일 `A.mid`, `B.mid` 가 폴더(`p01`, `p02` …)에 들어 있습니다. 같은 문장에서 만든 두 가지 버전이며, **어느 쪽이 어느 방식으로 만들어졌는지는 일부러 알려 드리지 않습니다.** A/B 의 배정은 곡마다 무작위입니다.

## 하는 일
1. 곡 폴더를 `p01` 부터 차례로 엽니다(순서도 무작위로 섞여 있습니다).
2. `A.mid` 와 `B.mid` 를 같은 재생 프로그램·같은 음색(악기 소리) 설정으로 들어 봅니다. 프로그램이 다르면 같은 파일도 소리가 달라지므로 한 곡의 A/B 는 반드시 같은 환경에서 듣습니다. 원하면 번갈아 여러 번 들어도 됩니다.
3. `score_sheet.csv` 의 해당 곡 줄에 **선호(A/B/동일)** 를 적고, 이유나 느낌이 있으면 **메모**에 적습니다. 판단이 어렵거나 차이를 못 느끼면 `동일` 로 적고 메모에 그렇다고 남겨 주세요.
4. 다 들었으면 `score_sheet.csv` 만 돌려 주세요. (평가 항목은 연구 1팀 루브릭이 정해지면 이 표에 열이 추가될 수 있습니다.)

## 지켜 주실 것
- 같은 묶음을 받은 다른 평가자와 답을 맞춰 보지 않습니다.
- 어느 쪽이 어느 방식인지 알아내려고 파일 정보(크기·수정 시각·파일 내부 데이터)를 살피지 않습니다. 파일 크기나 길이는 곡에 따라 차이가 날 수 있고 그것만으로 방식을 알 수는 없습니다.
- 이 폴더에 들어 있지 않은 정답표를 달라고 요청하지 않습니다(평가가 모두 끝난 뒤에 공개됩니다).

이 평가는 소리를 오디오로 변환하지 않고 MIDI 파일 그대로 듣는 평가입니다. 재생 프로그램의 음색이 인상에 영향을 줄 수 있습니다.
"""

KEY_README = """# 정답표 폴더 — 평가자에게 주지 마세요

이 폴더는 **블라인드를 유지하기 위해 평가자 폴더(`../for_raters/`)와 따로 보관**합니다.

- `key.csv`: 곡 번호(`p01`…)와 원래 id, A 와 B 가 각각 어느 쪽(v1/v2)인지. **평가자가 점수표를 다 돌려줄 때까지 공개하지 않습니다.**
- `excluded.csv`: 두 MIDI 가 바이트 동일해서 묶음에서 뺀 곡(들어도 차이가 없음).
- `pack_info.json`: 시드와 곡 수. 같은 시드·같은 입력이면 같은 묶음이 다시 만들어집니다(시드도 평가자에게 알리지 않는 편이 좋습니다. 시드를 알면 배정을 다시 계산할 수 있습니다).

## 블라인드를 지키는 방법
1. `for_raters/` 폴더만 압축해 평가자에게 보냅니다. `answer_key/` 는 별도 위치(평가자가 접근하지 못하는 곳)에 둡니다.
2. 평가자가 보낸 `score_sheet.csv` 를 받은 뒤에 열어 봅니다.
3. 집계: `score_sheet.csv` 의 `곡` 열과 `key.csv` 의 `piece` 열을 맞춘 뒤, 선호가 `A` 면 `key.csv` 의 `a_engine`, `B` 면 `b_engine` 이 선호된 쪽입니다(`동일` 은 어느 쪽도 아님). 표본이 적으면 우연일 수 있으니 곡 수·평가자 수와 함께 적습니다.
4. 평가자가 여러 명이면 같은 `for_raters/` 를 각자 따로 받게 하고 서로 의견을 나누지 않게 합니다.
5. 묶음을 다시 만들면 배정이 달라질 수 있는 입력(곡 목록·시드)이 바뀐 것인지 확인합니다. 같은 입력·같은 시드면 동일합니다.

## 한계
- 파일 크기·길이·연주 시간 차이로 어느 쪽인지 추측할 수 있는 곡이 있을 수 있습니다(평가자 안내에 알려 두었습니다).
- MIDI 를 오디오로 렌더하지 않았으므로 평가자의 재생 환경에 따라 인상이 달라집니다.
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def digest_key(seed: int, label: str, piece: str) -> str:
    return hashlib.sha256(f"{seed}:{label}:{piece}".encode()).hexdigest()


def find_pairs(folder: Path) -> tuple[dict[str, dict[str, Path]], list[str]]:
    """{id: {v1: path, v2: path}} 중 쌍이 완전한 것, 그리고 짝이 없는 파일 이름 목록."""
    found: dict[str, dict[str, Path]] = {}
    for p in sorted(folder.iterdir()):
        m = PAIR_RE.match(p.name)
        if m and p.is_file():
            found.setdefault(m["id"], {})[m["eng"]] = p
    complete = {i: d for i, d in found.items() if set(d) == set(ENGINES)}
    lonely = sorted(p.name for i, d in found.items() if i not in complete for p in d.values())
    return complete, lonely


def is_midi(p: Path) -> bool:
    with open(p, "rb") as f:
        return f.read(4) == b"MThd"


def assign(ids: list[str], seed: int) -> list[dict]:
    """곡 순서(p01…)와 A 가 v1 인지를 시드로 정한다. 정렬 기준이 해시라서 파이썬·플랫폼 버전과 무관하게 같다.
    A/B 는 '해시로 섞은 순서'에서 번갈아 배정해 정확히 반반(홀수면 한 곡 차이)이 된다."""
    order = sorted(ids, key=lambda i: (digest_key(seed, "order", i), i))
    flip = sorted(ids, key=lambda i: (digest_key(seed, "ab", i), i))
    a_is_v1 = {i: (pos % 2 == 0) for pos, i in enumerate(flip)}
    return [{"piece": f"p{n:02d}" if len(ids) < 100 else f"p{n:03d}", "id": i, "a": "v1" if a_is_v1[i] else "v2",
             "b": "v2" if a_is_v1[i] else "v1"} for n, i in enumerate(order, 1)]


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def write_pack(pairs: Path, out: Path, seed: int, include_identical: bool = False) -> dict:
    complete, lonely = find_pairs(pairs)
    bad = [f"{i}.{e}.mid" for i, d in complete.items() for e, p in d.items() if not is_midi(p)]
    if bad:
        raise SystemExit(f"MIDI 파일이 아닙니다(MThd 머리 없음): {', '.join(bad)}")
    identical = sorted(i for i, d in complete.items() if d["v1"].read_bytes() == d["v2"].read_bytes())
    use = sorted(i for i in complete if include_identical or i not in identical)
    if not use:
        raise SystemExit("묶을 쌍이 없습니다(쌍이 없거나 모든 쌍이 바이트 동일).")
    plan = assign(use, seed)

    rater, key = out / RATER_DIR, out / KEY_DIR
    for d in (rater, key):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for it in plan:
        (rater / it["piece"]).mkdir()
        for side in ("a", "b"):
            shutil.copyfile(complete[it["id"]][it[side]], rater / it["piece"] / f"{side.upper()}.mid")
    (rater / "score_sheet.csv").write_bytes(csv_bytes(SCORE_HEADER, [[it["piece"], "", ""] for it in plan]))
    (rater / "README.md").write_text(RATER_README, encoding="utf-8", newline="\n")

    (key / "key.csv").write_bytes(csv_bytes(
        ["piece", "original_id", "a_engine", "b_engine", "a_sha8", "b_sha8"],
        [[it["piece"], it["id"], it["a"], it["b"], sha(complete[it["id"]][it["a"]])[:8], sha(complete[it["id"]][it["b"]])[:8]] for it in plan]))
    (key / "excluded.csv").write_bytes(csv_bytes(["original_id", "reason"], [[i, "두 MIDI 가 바이트 동일"] for i in identical if i not in use] ))
    info = {"seed": seed, "pieces": len(plan), "a_is_v1": sum(1 for it in plan if it["a"] == "v1"), "a_is_v2": sum(1 for it in plan if it["a"] == "v2"),
            "excluded_identical": [i for i in identical if i not in use], "unpaired_files_ignored": lonely, "include_identical": include_identical}
    (key / "pack_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (key / "README.md").write_text(KEY_README, encoding="utf-8", newline="\n")
    return info


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", required=True, help="<id>.v1.mid / <id>.v2.mid 쌍이 들어 있는 폴더")
    ap.add_argument("--out", default="out/listening-pack", help="출력 폴더(기본 out/listening-pack). 안에 for_raters/ 와 answer_key/ 가 따로 만들어진다")
    ap.add_argument("--seed", type=int, required=True, help="배정 시드(정수). 같은 입력·같은 시드면 같은 묶음. 평가자에게 알리지 않는다")
    ap.add_argument("--include-identical", action="store_true", help="v1·v2 가 바이트 동일한 곡도 넣는다(기본은 뺀다)")
    args = ap.parse_args(argv)
    pairs = Path(args.pairs)
    if not pairs.is_dir():
        print(f"오류: 쌍 폴더가 아닙니다: {pairs}", file=out)
        return 2
    try:
        info = write_pack(pairs, Path(args.out), args.seed, args.include_identical)
    except SystemExit as exc:
        print(f"오류: {exc}", file=out)
        return 2
    print(f"{info['pieces']}곡 묶음을 {Path(args.out)} 에 썼습니다: 평가자에게는 {RATER_DIR}/ 만, {KEY_DIR}/ 는 평가가 끝날 때까지 주지 마세요."
          + (f" (바이트 동일해서 뺀 곡 {len(info['excluded_identical'])}개)" if info["excluded_identical"] else ""), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
