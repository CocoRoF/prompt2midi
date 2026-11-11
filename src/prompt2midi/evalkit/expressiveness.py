"""Expressiveness proxy indicators (3단계 기준선 P0). VALUES, not verdicts: no pass/fail, no threshold.

These numbers describe *measurable structure* in the MIDI (how much a melody repeats, how it moves, how chords
connect). They are proxies for properties that a listener judges as "expressive", and a better number is NOT a
better piece of music. Nothing here may be reported as music quality. Definitions are conservative first versions;
they follow the 연구 1팀 definition document once it arrives (not received at the time of writing).

Conventions (the compiler's, see compiler/render.py): melody = channel 0, chords = channel 2 (pad = channel 3 if there are
no chords), drums = channel 9 (excluded from every pitch measure). MIDI from other generators that do not follow this
yield None for the indicators that need the melody/chord channel. Section borders need the MusicSpec; without it the
two section indicators are None. Every indicator returns a float rounded to 4 places, or None when it cannot be
measured. Same input -> same output (no randomness).
"""
from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import mido

from ..spec import MODE_INTERVALS, PITCH_CLASS, MusicSpec

DRUM_CHANNEL = 9
MELODY_CHANNEL = 0
CHORD_CHANNELS = (2, 3)  # chords first, pad as the fallback
NGRAM_INTERVALS = 3      # a repeated figure = the same 3 successive intervals (4 notes), transposition-invariant
STEP_MAX = 2             # step: 1-2 semitones
LEAP_MIN = 5             # leap: >= 5 semitones (a fourth or more)
CONTOUR_CLASSES = ("flat", "arch", "valley", "ascending", "descending")


@dataclass(frozen=True)
class Note:
    start: int
    end: int
    pitch: int
    channel: int


@dataclass
class Material:
    notes: list[Note]
    ppq: int
    bar_ticks: int
    sections: list[tuple[int, int]] | None   # tick ranges, from the spec
    tonic_pc: int | None
    mode: str | None
    scale_label: bool                        # True when tonic/mode come from the answer label, False when from the spec


def _notes_of(mid: mido.MidiFile) -> list[Note]:
    out: list[Note] = []
    for tr in mid.tracks:
        t = 0
        open_: dict[tuple[int, int], list[int]] = {}
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                open_.setdefault((m.channel, m.note), []).append(t)
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                starts = open_.get((m.channel, m.note))
                if starts:
                    out.append(Note(starts.pop(0), t, m.note, m.channel))
    out.sort(key=lambda n: (n.start, n.pitch, n.channel, n.end))
    return out


def material_from_midi(midi: bytes, spec: MusicSpec | None = None, expect=None) -> Material | None:
    """Parse once. Returns None when the bytes are not a readable MIDI file."""
    try:
        mid = mido.MidiFile(file=io.BytesIO(midi))
    except Exception:  # noqa: BLE001 - mido raises many kinds; an unreadable file simply has no measurements
        return None
    num, den = 4, 4
    for tr in mid.tracks:
        for m in tr:
            if m.type == "time_signature":
                num, den = m.numerator, m.denominator
                break
        else:
            continue
        break
    ppq = mid.ticks_per_beat
    bar_ticks = max(1, round(ppq * num * 4 / den))
    sections = None
    if spec is not None:
        sections, pos = [], 0
        for s in spec.sections:
            sections.append((pos, pos + s.bars * bar_ticks))
            pos += s.bars * bar_ticks
    tonic_pc = mode = None
    label = False
    if expect is not None and expect.tonic is not None and expect.mode is not None:
        tonic_pc, mode, label = PITCH_CLASS[expect.tonic.value], expect.mode.value, True
    elif spec is not None:
        tonic_pc, mode = PITCH_CLASS[spec.key.tonic.value], spec.key.mode.value
    elif expect is not None and expect.tonic is not None:
        tonic_pc, label = PITCH_CLASS[expect.tonic.value], True   # tonic only: enough for the cadence indicator
    return Material(_notes_of(mid), ppq, bar_ticks, sections, tonic_pc, mode, label)


def _melody(m: Material) -> list[Note]:
    """Channel 0, one note per onset (the highest if several start together), in time order."""
    best: dict[int, Note] = {}
    for n in m.notes:
        if n.channel == MELODY_CHANNEL and (n.start not in best or n.pitch > best[n.start].pitch):
            best[n.start] = n
    return [best[s] for s in sorted(best)]


def _pitched(m: Material) -> list[Note]:
    return [n for n in m.notes if n.channel != DRUM_CHANNEL]


def _r(x: float) -> float:
    return round(x, 4) + 0.0   # + 0.0 turns -0.0 into 0.0


def _bars(melody: list[Note], bar_ticks: int) -> dict[int, list[Note]]:
    bars: dict[int, list[Note]] = {}
    for n in melody:
        bars.setdefault(n.start // bar_ticks, []).append(n)
    return bars


# ---------------------------------------------------------------- indicators

def melody_repetition(m: Material) -> float | None:
    """Share of 4-note figures of the melody that occur at least twice.

    Counts every window of 4 successive melody notes, described by its 3 intervals (so a figure repeated at another
    pitch still counts). 0 = no figure ever returns, 1 = every figure returns. None for fewer than 4 notes.
    Stands in for: motif repetition / "does the tune come back". Limits: ignores rhythm and note length; a melody
    stuck on one pitch scores 1.0; a high value can mean catchy or merely monotonous; says nothing about whether the
    repeats are placed musically.
    """
    p = [n.pitch for n in _melody(m)]
    if len(p) < NGRAM_INTERVALS + 1:
        return None
    iv = [b - a for a, b in zip(p, p[1:])]
    grams = [tuple(iv[i:i + NGRAM_INTERVALS]) for i in range(len(iv) - NGRAM_INTERVALS + 1)]
    c = Counter(grams)
    return _r(sum(1 for g in grams if c[g] >= 2) / len(grams))


def melody_variation(m: Material) -> float | None:
    """How much the melody changes between bars that have the same rhythm.

    Takes every pair of bars (>= 2 notes) whose note onsets sit at the same positions inside the bar and measures the
    share of positions where the pitch differs; the value is the mean over those pairs. 0 = such bars are exact copies,
    1 = they never share a pitch. None when no two bars share a rhythm. Stands in for: variation of a repeated rhythmic
    pattern. Limits: only compares bars with an identical rhythm (a varied rhythm is invisible to it); a pitch change is
    counted the same whether it is a small embellishment or a different tune.
    """
    bars = _bars(_melody(m), m.bar_ticks)
    sig = {b: (tuple(n.start % m.bar_ticks for n in ns), [n.pitch for n in ns]) for b, ns in bars.items() if len(ns) >= 2}
    keys = sorted(sig)
    diffs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if sig[a][0] == sig[b][0]:
                pa, pb = sig[a][1], sig[b][1]
                diffs.append(sum(x != y for x, y in zip(pa, pb)) / len(pa))
    return _r(sum(diffs) / len(diffs)) if diffs else None


def _intervals(m: Material) -> list[int]:
    p = [n.pitch for n in _melody(m)]
    return [abs(b - a) for a, b in zip(p, p[1:])]


def interval_step_ratio(m: Material) -> float | None:
    """Share of successive melody intervals that are steps (1-2 semitones). Repeated notes (0) and skips (3-4) are in neither
    this nor the leap ratio. Stands in for: smoothness / singability of the line. Limits: depends on the key's scale (a
    2-semitone move is a step in any mode, but 3 semitones is a step in a pentatonic tune); says nothing about phrasing."""
    iv = _intervals(m)
    return _r(sum(1 <= i <= STEP_MAX for i in iv) / len(iv)) if iv else None


def interval_leap_ratio(m: Material) -> float | None:
    """Share of successive melody intervals that are leaps (>= 5 semitones). Stands in for: angularity / drama of the line.
    Limits: a leap down to a chord tone and an arbitrary jump count the same; octave jumps in a generated line may be
    register wrapping rather than a musical choice."""
    iv = _intervals(m)
    return _r(sum(i >= LEAP_MIN for i in iv) / len(iv)) if iv else None


def _bar_contour(pitches: list[int]) -> str:
    hi, lo = max(pitches), min(pitches)
    if hi == lo:
        return "flat"
    ih, il, first, last = pitches.index(hi), pitches.index(lo), pitches[0], pitches[-1]
    if 0 < ih < len(pitches) - 1 and hi > max(first, last):
        return "arch"
    if 0 < il < len(pitches) - 1 and lo < min(first, last):
        return "valley"
    if last > first:
        return "ascending"
    if last < first:
        return "descending"
    return "flat"


def contour_diversity(m: Material) -> float | None:
    """Normalised entropy (0..1) of the per-bar melodic shape: flat / arch / valley / ascending / descending.

    Only bars with >= 3 melody notes count; needs >= 2 such bars (else None). 0 = every bar has the same shape,
    1 = the five shapes are used equally often. Stands in for: variety of melodic gesture. Limits: five coarse shapes;
    bar boundaries are not phrase boundaries; a random walk is also "diverse".
    """
    shapes = [_bar_contour([n.pitch for n in ns]) for ns in _bars(_melody(m), m.bar_ticks).values() if len(ns) >= 3]
    if len(shapes) < 2:
        return None
    c = Counter(shapes)
    h = -sum(v / len(shapes) * math.log(v / len(shapes)) for v in c.values())
    return _r(h / math.log(len(CONTOUR_CLASSES)))


def final_is_tonic(m: Material) -> float | None:
    """1.0 if the last melody note has the pitch class of the key's tonic, else 0.0 (None without a melody or a tonic).

    The tonic is the spec's declared key, or the answer label's tonic when the evaluation set gives one. Stands in for:
    melodic closure (a tune that ends "at home"). Limits: a single yes/no; ending on the fifth or third can be a
    deliberate open ending; it does not look at the harmony under the last note or at its duration.
    """
    mel = _melody(m)
    if not mel or m.tonic_pc is None:
        return None
    return 1.0 if mel[-1].pitch % 12 == m.tonic_pc else 0.0


def section_density_gap(m: Material) -> float | None:
    """Largest minus smallest note density between sections (non-drum note onsets per beat). Needs the spec's sections
    (>= 2), else None. Stands in for: dynamics of arrangement - does a later section get busier or sparser.
    Limits: counts notes, not loudness or timbre; a section made of one long chord and one of many short ones differ
    here even if they sound similar; sections are the spec's, not what a listener hears as sections."""
    if not m.sections or len(m.sections) < 2:
        return None
    notes = _pitched(m)
    dens = []
    for lo, hi in m.sections:
        beats = (hi - lo) / m.ppq
        dens.append(sum(lo <= n.start < hi for n in notes) / beats if beats else 0.0)
    return _r(max(dens) - min(dens))


def section_register_gap(m: Material) -> float | None:
    """Largest minus smallest mean melody pitch between sections, in semitones. Needs the spec's sections (>= 2 with melody
    notes), else None. Stands in for: register contrast (a "lift" into a higher section). Limits: the mean hides the
    shape; one outlier note moves it; contrast in register is not contrast in expression."""
    if not m.sections:
        return None
    mel = _melody(m)
    means = []
    for lo, hi in m.sections:
        ps = [n.pitch for n in mel if lo <= n.start < hi]
        if ps:
            means.append(sum(ps) / len(ps))
    return _r(max(means) - min(means)) if len(means) >= 2 else None


def chord_voice_distance(m: Material) -> float | None:
    """Mean distance, in semitones per voice, between consecutive different chords of the chord channel.

    A chord = the notes that start at the same tick on channel 2 (channel 3 if there is no chord channel). Consecutive
    identical chords are merged; voices are matched by order from the bottom (sorted pitches), over the smaller chord. 0 =
    chords never change shape; small = smooth voice leading; large = chords jump around. None with fewer than two different
    chords. Stands in for: voice-leading smoothness. Limits: matching by sorted order is a rough voice assignment (not an
    optimal one); smooth is not the same as good - a static pad is smooth too.
    """
    for ch in CHORD_CHANNELS:
        by_start: dict[int, list[int]] = {}
        for n in m.notes:
            if n.channel == ch:
                by_start.setdefault(n.start, []).append(n.pitch)
        if by_start:
            chords: list[list[int]] = []
            for s in sorted(by_start):
                c = sorted(by_start[s])
                if not chords or chords[-1] != c:
                    chords.append(c)
            dists = []
            for a, b in zip(chords, chords[1:]):
                k = min(len(a), len(b))
                dists.append(sum(abs(a[i] - b[i]) for i in range(k)) / k)
            return _r(sum(dists) / len(dists)) if dists else None
    return None


def off_scale_rate(m: Material) -> float | None:
    """Share of pitched (non-drum) notes outside the key's scale. The key is the answer label's tonic+mode if the set gives
    both (same basis as the `scale_deviation` metric), else the spec's declared key; None if neither. Stands in for: tonal
    stability / how "in key" it is. Limits: chromatic passing tones, borrowed chords and modal mixture are penalised
    although they can be expressive; against the declared key a generator that only uses the scale scores 0 trivially."""
    if m.tonic_pc is None or m.mode is None:
        return None
    pitched = _pitched(m)
    if not pitched:
        return None
    scale = {(m.tonic_pc + i) % 12 for i in MODE_INTERVALS[m.mode]}
    return _r(sum(n.pitch % 12 not in scale for n in pitched) / len(pitched))


@dataclass(frozen=True)
class Indicator:
    name: str          # CSV column = "expr_" + name
    title: str
    fn: Callable[[Material], float | None]


INDICATORS: tuple[Indicator, ...] = (
    Indicator("melody_repetition", "선율 반복률", melody_repetition),
    Indicator("melody_variation", "반복 리듬 마디의 변주 정도", melody_variation),
    Indicator("step_ratio", "순차 진행 비율", interval_step_ratio),
    Indicator("leap_ratio", "도약 비율", interval_leap_ratio),
    Indicator("contour_diversity", "윤곽 다양성", contour_diversity),
    Indicator("final_is_tonic", "종지음이 으뜸음", final_is_tonic),
    Indicator("section_density_gap", "섹션 간 밀도 차", section_density_gap),
    Indicator("section_register_gap", "섹션 간 음역(평균 음높이) 차", section_register_gap),
    Indicator("chord_voice_distance", "코드 간 평균 이동 거리(음/성부)", chord_voice_distance),
    Indicator("off_scale_rate", "음계 이탈률", off_scale_rate),
)
COLUMN_PREFIX = "expr_"


def measure(midi: bytes | None, spec: MusicSpec | None = None, expect=None) -> dict[str, float | None]:
    """All indicators for one piece: {name: value or None}. Unreadable or missing MIDI gives all None."""
    mat = material_from_midi(midi, spec, expect) if midi else None
    return {i.name: (i.fn(mat) if mat is not None else None) for i in INDICATORS}
