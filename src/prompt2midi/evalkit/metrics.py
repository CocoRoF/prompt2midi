"""Automatic metrics. Each returns Result(status, reason) with status in {pass, fail, na}.

pass/fail only when the question applies; 'na' means "no label / not applicable" and is left out of the
denominator. Mood fit and musicality are deliberately NOT here (see LISTEN_COLUMNS).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Callable

import mido

from ..spec import GM_PROGRAM, INSTRUMENT_RANGE, MODE_INTERVALS, PITCH_CLASS, MusicSpec, total_bars
from ..validate import check_midi

PASS, FAIL, NA = "pass", "fail", "na"
DEFAULT_BARS = 12
MIN_BARS = 8
TEMPO_TOLERANCE_BPM = 0.5
OUT_OF_RANGE_MAX = 0.05   # 연구 1팀 1-2: 악기 음역 밖 노트 5% 이하
OFF_SCALE_MAX = 0.10      # 연구 1팀 2-3: 조 구성음 밖 노트 10% 이하 (재즈·블루스 25% 규칙은 장르 라벨이 없어 적용하지 않음)
DRUM_CHANNEL = 9
PROGRAM_TO_INSTRUMENT = {v: k for k, v in GM_PROGRAM.items()}
LISTEN_COLUMNS = ("mood_listen", "musicality_listen")  # always "청취 필요": a human rubric scores these


@dataclass(frozen=True)
class Result:
    status: str
    reason: str = ""


@dataclass
class Context:
    """Everything a metric may look at for one prompt. spec/midi are None when generation failed."""

    expect: object | None
    spec: MusicSpec | None
    midi: bytes | None
    error: str = ""
    deterministic: bool | None = None  # None = not checked


def _tempo_in_midi(midi: bytes) -> float | None:
    for tr in mido.MidiFile(file=io.BytesIO(midi)).tracks:
        for m in tr:
            if m.type == "set_tempo":
                return mido.tempo2bpm(m.tempo)
    return None


def _signature_in_midi(midi: bytes) -> str | None:
    for tr in mido.MidiFile(file=io.BytesIO(midi)).tracks:
        for m in tr:
            if m.type == "time_signature":
                return f"{m.numerator}/{m.denominator}"
    return None


def _analyse(midi: bytes) -> dict:
    """One pass over the MIDI: sounding notes per channel, program per channel, notes per track."""
    notes: dict[int, list[int]] = {}
    programs: dict[int, int] = {}
    empty_tracks = 0
    for tr in mido.MidiFile(file=io.BytesIO(midi)).tracks:
        has_note = has_meta = False
        for m in tr:
            if m.type == "note_on" and m.velocity > 0:
                notes.setdefault(m.channel, []).append(m.note)
                has_note = True
            elif m.type == "program_change":
                programs.setdefault(m.channel, m.program)
            elif m.type in ("set_tempo", "time_signature"):
                has_meta = True
        if not has_note and not has_meta:
            empty_tracks += 1
    return {"notes": notes, "programs": programs, "empty_tracks": empty_tracks}


def _frac(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0"


def _label(ctx: Context, name: str):
    return getattr(ctx.expect, name, None) if ctx.expect is not None else None


def _failed(ctx: Context) -> Result | None:
    return Result(FAIL, f"생성 실패: {ctx.error}") if ctx.spec is None or ctx.midi is None else None


def tempo_match(ctx: Context) -> Result:
    want = _label(ctx, "tempo_bpm")
    if want is None:
        return Result(NA, "tempo 라벨 없음")
    if (f := _failed(ctx)):
        return f
    got = _tempo_in_midi(ctx.midi)
    if got is None:
        return Result(FAIL, "MIDI 에 set_tempo 가 없음")
    ok = abs(got - want) <= TEMPO_TOLERANCE_BPM
    return Result(PASS if ok else FAIL, f"MIDI {got:.2f} BPM, 라벨 {want} (허용 ±{TEMPO_TOLERANCE_BPM})")


def key_match(ctx: Context) -> Result:
    tonic, mode = _label(ctx, "tonic"), _label(ctx, "mode")
    if tonic is None and mode is None:
        return Result(NA, "tonic·mode 라벨 없음")
    if (f := _failed(ctx)):
        return f
    got_t, got_m = ctx.spec.key.tonic.value, ctx.spec.key.mode.value
    bad = []
    if tonic is not None and PITCH_CLASS[tonic.value] != PITCH_CLASS[got_t]:
        bad.append(f"tonic {got_t} != {tonic.value}")
    if mode is not None and mode.value != got_m:
        bad.append(f"mode {got_m} != {mode.value}")
    return Result(FAIL if bad else PASS, ", ".join(bad) or f"사양 {got_t} {got_m}")


def time_signature_match(ctx: Context) -> Result:
    want = _label(ctx, "time_signature")
    if want is None:
        return Result(NA, "time_signature 라벨 없음")
    if (f := _failed(ctx)):
        return f
    got = _signature_in_midi(ctx.midi)
    return Result(PASS if got == want else FAIL, f"MIDI {got}, 라벨 {want}")


def bars_match(ctx: Context) -> Result:
    want = _label(ctx, "bars")
    if want is None:
        return Result(NA, "bars 라벨 없음")
    if (f := _failed(ctx)):
        return f
    got = total_bars(ctx.spec)
    return Result(PASS if got == want else FAIL, f"{got}마디, 라벨 {want}")


def bars_min(ctx: Context) -> Result:
    """Length rule when the prompt gives no bar count: default 12, at least 8 (합의안 Q3). Violation = fewer than 8."""
    if _label(ctx, "bars") is not None:
        return Result(NA, "bars 를 명시한 프롬프트(bars_match 로 채점)")
    if (f := _failed(ctx)):
        return f
    got = total_bars(ctx.spec)
    note = "" if got == DEFAULT_BARS else f" (기본 {DEFAULT_BARS}마디와 다름)"
    return Result(PASS if got >= MIN_BARS else FAIL, f"{got}마디, 최소 {MIN_BARS}{note}")


def instruments_included(ctx: Context) -> Result:
    want = _label(ctx, "instruments")
    if want is None:
        return Result(NA, "instruments 라벨 없음")
    if (f := _failed(ctx)):
        return f
    have = {t.instrument.value for t in ctx.spec.tracks if t.instrument is not None}
    if any(t.role.value == "drums" for t in ctx.spec.tracks):
        have.add("drums")
    missing = [w for w in want if w not in have]
    return Result(FAIL if missing else PASS, f"빠진 악기 {missing}" if missing else f"트랙 악기 {sorted(have)}")


def pitch_range(ctx: Context) -> Result:
    """연구 1팀 1-2: notes outside the real range of their instrument (instrument = the GM program of the channel)."""
    if (f := _failed(ctx)):
        return f
    a = _analyse(ctx.midi)
    total = out = 0
    for ch, pitches in a["notes"].items():
        if ch == DRUM_CHANNEL:
            continue
        rng = INSTRUMENT_RANGE.get(PROGRAM_TO_INSTRUMENT.get(a["programs"].get(ch, -1), ""), (0, 127))
        total += len(pitches)
        out += sum(1 for p in pitches if not rng[0] <= p <= rng[1])
    if total == 0:
        return Result(NA, "음높이가 있는 음표가 없음")
    return Result(PASS if out / total <= OUT_OF_RANGE_MAX else FAIL, f"악기 음역 밖 {_frac(out, total)}, 허용 ≤{OUT_OF_RANGE_MAX:.0%}")


def note_integrity(ctx: Context) -> Result:
    """연구 1팀 1-3: every NoteOn has a NoteOff, no zero-length notes (MidiCheck reports those as a warning; counted here)."""
    if (f := _failed(ctx)):
        return f
    bad = [i for i in check_midi(ctx.midi, ctx.spec) if i.code in ("unmatched_note_off", "unterminated_note", "zero_length_note", "malformed_track")]
    return Result(FAIL if bad else PASS, "; ".join(f"{i.path}: {i.message}" for i in bad[:3]) or "짝 안 맞는 음·길이 0 음 없음")


def no_empty_tracks(ctx: Context) -> Result:
    """연구 1팀 1-8 (빈 트랙 0): a track that is neither the tempo/meter track nor holds any note."""
    if (f := _failed(ctx)):
        return f
    n = _analyse(ctx.midi)["empty_tracks"]
    return Result(FAIL if n else PASS, f"빈 트랙 {n}개")


def midi_instruments_included(ctx: Context) -> Result:
    """연구 1팀 3-7 / 1-8: labelled instruments vs the program changes (and the drum channel) in the MIDI itself."""
    want = _label(ctx, "instruments")
    if want is None:
        return Result(NA, "instruments 라벨 없음")
    if (f := _failed(ctx)):
        return f
    a = _analyse(ctx.midi)
    have = {PROGRAM_TO_INSTRUMENT[a["programs"][ch]] for ch in a["notes"] if ch != DRUM_CHANNEL and a["programs"].get(ch) in PROGRAM_TO_INSTRUMENT}
    if DRUM_CHANNEL in a["notes"]:
        have.add("drums")
    missing = [w for w in want if w not in have]
    return Result(FAIL if missing else PASS, f"MIDI 에 빠진 악기 {missing}" if missing else f"MIDI 프로그램 {sorted(have)}")


def scale_deviation(ctx: Context) -> Result:
    """연구 1팀 2-3: share of pitched notes outside the scale of the LABELLED key (not the declared one)."""
    tonic, mode = _label(ctx, "tonic"), _label(ctx, "mode")
    if tonic is None or mode is None:
        return Result(NA, "tonic·mode 라벨이 둘 다 있어야 채점")
    if (f := _failed(ctx)):
        return f
    scale = {(PITCH_CLASS[tonic.value] + i) % 12 for i in MODE_INTERVALS[mode.value]}
    pitches = [p for ch, ps in _analyse(ctx.midi)["notes"].items() if ch != DRUM_CHANNEL for p in ps]
    if not pitches:
        return Result(NA, "음높이가 있는 음표가 없음")
    out = sum(1 for p in pitches if p % 12 not in scale)
    return Result(PASS if out / len(pitches) <= OFF_SCALE_MAX else FAIL, f"음계 밖 {_frac(out, len(pitches))}, 허용 ≤{OFF_SCALE_MAX:.0%}")


def midicheck_valid(ctx: Context) -> Result:
    if (f := _failed(ctx)):
        return f
    errors = [i for i in check_midi(ctx.midi, ctx.spec) if i.severity == "error"]
    return Result(FAIL if errors else PASS, "; ".join(f"{i.path}: {i.message}" for i in errors[:3]) or "MidiCheck 오류 0건")


def deterministic(ctx: Context) -> Result:
    if ctx.deterministic is None:
        return Result(NA, "결정성 검사를 하지 않음")
    if ctx.midi is None:
        return Result(NA, "생성 실패라 비교할 바이트가 없음")
    return Result(PASS if ctx.deterministic else FAIL, "같은 입력 2회 바이트 동일" if ctx.deterministic else "같은 입력인데 바이트가 다름")


@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    fn: Callable[[Context], Result]


# Order = column order. Add new metrics here (e.g. Intent v1 field match, once its schema exists).
METRICS: tuple[Metric, ...] = (
    Metric("tempo_match", "템포 일치", tempo_match),
    Metric("key_match", "조성 일치", key_match),
    Metric("time_signature_match", "박자표 일치", time_signature_match),
    Metric("bars_match", "마디 수 일치", bars_match),
    Metric("bars_min", "길이 규칙(≥8마디)", bars_min),
    Metric("instruments_included", "악기 포함", instruments_included),
    Metric("midi_instruments_included", "악기 포함(MIDI 프로그램)", midi_instruments_included),
    Metric("scale_deviation", "음계 이탈음(라벨 조 기준)", scale_deviation),
    Metric("pitch_range", "악기 음역", pitch_range),
    Metric("note_integrity", "음 짝·길이", note_integrity),
    Metric("no_empty_tracks", "빈 트랙 없음", no_empty_tracks),
    Metric("midicheck_valid", "MidiCheck 유효", midicheck_valid),
    Metric("deterministic", "결정성", deterministic),
)
