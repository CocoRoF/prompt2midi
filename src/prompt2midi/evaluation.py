"""Run the RuleProvider pipeline over an evaluation prompt file and measure what text2midi's baseline measured.

    python -m prompt2midi.evaluation data/eval_prompts_research1.json --out data/eval_rule_results.csv --md docs/rule-provider-eval.md

Offline only: RuleProvider + validate + compile + MidiCheck. No network, no keys.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import resource
import statistics
import time
import platform
import tracemalloc
from pathlib import Path

import mido

from .pipeline import generate
from .providers import RuleProvider
from .spec import MODE_INTERVALS, PITCH_CLASS

# text2midi baseline, measured by 연구 1팀 (docs/프롬프트투미디/text2midi-기준선.md, 기준일 2026-11-10)
BASELINE = {
    "gen_seconds_mean": 100.1, "gen_seconds_range": "88.3~123.5", "peak_mem_mb": 1277,
    "duration_seconds_mean": 9.6, "duration_seconds_range": "3.0~15.6", "notes_range": "15~106",
    "tempo_121_of_10": 9, "key_match_of_10": 3,
}

COLUMNS = ["id", "mood", "expected_bpm", "expected_key", "valid", "provider", "attempts", "ms_median", "ms_runs",
           "tracemalloc_peak_kb", "midi_bytes", "duration_s", "notes", "tempo_in_midi", "tempo_match",
           "key_declared", "key_match", "melody_in_declared_scale", "sha256_8"]


def _key_of(text: str) -> tuple[int, str]:
    tonic, mode = text.split(" ", 1)
    return PITCH_CLASS[tonic], mode.replace(" ", "_")


def _analyse(midi: bytes) -> dict:
    mid = mido.MidiFile(file=io.BytesIO(midi))
    tempos, notes, melody = [], 0, []
    for tr in mid.tracks:
        for m in tr:
            if m.type == "set_tempo":
                tempos.append(round(mido.tempo2bpm(m.tempo), 2))
            elif m.type == "note_on" and m.velocity > 0:
                notes += 1
                if m.channel == 0:
                    melody.append(m.note % 12)
    return {"duration_s": round(mid.length, 2), "notes": notes, "tempo": tempos[0] if tempos else None, "melody_pcs": melody}


def evaluate(prompts: list[dict], runs: int = 5) -> list[dict]:
    rows = []
    prov = RuleProvider()
    for p in prompts:
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            result = generate(p["text"], provider=prov, fallback=False)
            times.append((time.perf_counter() - t0) * 1000)
        tracemalloc.start()
        result = generate(p["text"], provider=prov, fallback=False)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        a = _analyse(result.midi)
        want_pc, want_mode = _key_of(p["key"]) if p.get("key") else (None, None)
        sp = result.spec
        decl_pc, decl_mode = PITCH_CLASS[sp.key.tonic.value], sp.key.mode.value
        scale = {(decl_pc + i) % 12 for i in MODE_INTERVALS[decl_mode]}
        in_scale = (sum(1 for pc in a["melody_pcs"] if pc in scale) / len(a["melody_pcs"])) if a["melody_pcs"] else 0.0
        rows.append({
            "id": p["id"], "mood": p.get("mood", ""), "expected_bpm": p.get("bpm", ""), "expected_key": p.get("key", ""),
            "valid": True, "provider": result.provider_used, "attempts": len(result.attempts),
            "ms_median": round(statistics.median(times), 2), "ms_runs": runs, "tracemalloc_peak_kb": round(peak / 1024),
            "midi_bytes": len(result.midi), "duration_s": a["duration_s"], "notes": a["notes"], "tempo_in_midi": a["tempo"],
            "tempo_match": (a["tempo"] == p["bpm"]) if p.get("bpm") else "",
            "key_declared": f"{sp.key.tonic.value} {decl_mode}",
            "key_match": ((decl_pc, decl_mode) == (want_pc, want_mode)) if want_pc is not None else "",
            "melody_in_declared_scale": round(in_scale, 3), "sha256_8": hashlib.sha256(result.midi).hexdigest()[:8],
        })
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def summary_md(rows: list[dict], prompts_path: str) -> str:
    n = len(rows)
    ms = [r["ms_median"] for r in rows]
    dur = [r["duration_s"] for r in rows]
    notes = [r["notes"] for r in rows]
    tm = sum(1 for r in rows if r["tempo_match"] is True)
    km = sum(1 for r in rows if r["key_match"] is True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return f"""# RuleProvider 평가 (text2midi 기준선과 같은 프롬프트 {n}개)

- 측정 환경: Python {platform.python_version()}, {platform.system()} {platform.machine()}, 시간·메모리는 이 작업대에서 잰 값(CPU 모델명 미확인). 날짜 {time.strftime('%Y-%m-%d')}.
- 입력: `{prompts_path}` (연구 1팀 프롬프트 10개 사본). 생성: `python -m prompt2midi.evaluation`. 원자료: `data/eval_rule_results.csv`.
- 우리 쪽은 **RuleProvider(키워드 사전) + 검증 + 컴파일러 + MidiCheck**, 외부 호출 없음. 사양 해석은 LLM 이 아니라 규칙이다.
- 기준선 값은 모두 연구 1팀 측정(docs/프롬프트투미디/text2midi-기준선.md). **조건이 다르다**: text2midi 는 신경망 생성(CPU 12코어, 메모리 상한 1,638MB, max_len 512, seed 0), 이쪽은 규칙 번역이라 두 수치는 속도·메모리의 직접 비교 대상이 아니다.

| 지표 | text2midi (연구 1팀) | RuleProvider + 컴파일러 (이번 측정) |
|---|---|---|
| 유효 MIDI | 기준선 문서에 유효 비율 기록 없음 | {n}/{n} (MidiCheck 오류 0건) |
| 프롬프트당 생성 시간 | 평균 {BASELINE['gen_seconds_mean']}초 ({BASELINE['gen_seconds_range']}) | 중앙값 {statistics.median(ms):.1f}ms (최소 {min(ms):.1f}~최대 {max(ms):.1f}ms, 프롬프트당 5회 중앙값) |
| 메모리 | 최대 {BASELINE['peak_mem_mb']}MB | Python 할당 최대 {max(r['tracemalloc_peak_kb'] for r in rows)}KB(프롬프트당, tracemalloc) / 프로세스 최대 RSS {rss:.0f}MB(인터프리터 포함) |
| 곡 길이 | 3.0~15.6초 (평균 {BASELINE['duration_seconds_mean']}초) | {min(dur):.1f}~{max(dur):.1f}초 (평균 {statistics.mean(dur):.1f}초) |
| 노트 수 | {BASELINE['notes_range']} | {min(notes)}~{max(notes)} |
| 명시한 템포를 지켰나 | 10개 중 9개가 121.3 BPM (지키지 못함) | {tm}/{n} 일치(MIDI 템포 메타 기준) |
| 명시한 조성 일치 | 자동 추정으로 3/10 (추정 방식 미공개) | {km}/{n} 일치(사양에 선언된 조성 기준) |
| 선율이 선언 음계 안인 비율 | 측정 안 함 | 평균 {statistics.mean(r['melody_in_declared_scale'] for r in rows)*100:.1f}% |
| 무드 부합 / 청취 | 측정 안 함 | **측정 안 함 — 듣지 못했다** |

## 읽는 법과 한계
- 템포·조성 일치는 프롬프트에 BPM·조성이 **글자로 적혀 있어** 규칙이 그 글자를 읽은 결과다. 사양에 선언한 값이 컴파일러에서 보장되는 구조의 효과이지, 문장 이해력의 증거가 아니다. 프롬프트가 그런 정보를 적지 않으면 규칙은 무드 사전의 기본값을 쓴다(`RuleProvider.explain` 으로 확인).
- 조성 일치는 기준선의 "자동 추정"과 방법이 다르다(우리는 선언 값, 기준선은 생성 MIDI에서 추정). 같은 조건 비교가 아니다.
- 곡 길이는 사양(섹션 12마디)이 정한다. 모델이 길이를 못 늘리는 기준선과 단순 비교하지 말 것.
- 음악 품질·무드 표현은 측정하지 못했다. 규칙이 만든 곡은 코드 3개 섹션을 반복하는 단순한 구조다.
- 프롬프트 10개는 같은 형식의 문장이라 규칙 사전이 맞추기 쉽다. 다른 형식의 프롬프트에서의 성능은 미측정.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prompts")
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--midi-dir")
    args = ap.parse_args(argv)
    prompts = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
    rows = evaluate(prompts, args.runs)
    if args.out:
        write_csv(rows, Path(args.out))
    if args.md:
        Path(args.md).write_text(summary_md(rows, args.prompts), encoding="utf-8")
    if args.midi_dir:
        Path(args.midi_dir).mkdir(parents=True, exist_ok=True)
        for p in prompts:
            Path(args.midi_dir, f"prompt{p['id']:02d}.mid").write_bytes(generate(p["text"], provider=RuleProvider(), fallback=False).midi)
    print(summary_md(rows, args.prompts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
