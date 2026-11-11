"""Automatic metrics. Each returns Result(status, reason) with status in {pass, fail, na}.

pass/fail only when the question applies; 'na' means "no label / not applicable" and is left out of the
denominator. Mood fit and musicality are deliberately NOT here (see LISTEN_COLUMNS).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Callable

import mido

from ..spec import PITCH_CLASS, MusicSpec, total_bars
from ..validate import check_midi

PASS, FAIL, NA = "pass", "fail", "na"
DEFAULT_BARS = 12
MIN_BARS = 8
TEMPO_TOLERANCE_BPM = 0.5
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
    Metric("midicheck_valid", "MidiCheck 유효", midicheck_valid),
    Metric("deterministic", "결정성", deterministic),
)
