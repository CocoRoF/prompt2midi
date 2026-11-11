"""Automatic proxy indicators M1..M7 for musical expressiveness (연구 1팀 정의서 `자동_대리지표_정의.md`, 선지우, 2026-11-11).

READ THIS FIRST
  * These are PROXIES, not quality scores. They measure properties (how much a melody repeats, how often it leaps,
    how far chords move) and say whether the value lies in a RANGE. The verdict names are therefore
    `in_range` / `out_of_range` / `na`, never pass/fail, and they are never mixed into the pass/fail aggregates
    or into `axis1_ok`.
  * **Every threshold below is a TEMPORARY value (no source, not calibrated). Until a listening correlation study
    (Spearman |rho| >= 0.3 on 30+ pieces, 정의서 10절) has been done, nothing here may be used as evidence of musical quality
    or as a sign-off metric.** Use it to compare before/after a rule change and to catch regressions, not to rank pieces
    or as an optimisation target (a number can be reached without the music improving).
  * All thresholds live in ONE table, THRESH. Changing a value does not change a definition.
  * Pieces that failed `midicheck_valid` or `note_integrity` get `na` for every indicator (reason "유효성 실패").

Inputs: the MIDI bytes; the MusicSpec when the output is ours (sections, chord names, melody role, key); the evaluation
set's tonic/mode label as a fallback key. The key is NEVER estimated from the MIDI. Drum-channel notes are excluded
everywhere. Pure functions, no randomness: same input -> same output (4-decimal rounding).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from itertools import permutations
from statistics import median
from typing import Any

import mido

from ..spec import PITCH_CLASS, MusicSpec, parse_chord

IN_RANGE, OUT_OF_RANGE, NA = "in_range", "out_of_range", "na"
DRUM_CHANNEL = 9
MELODY_CHANNEL = 0  # the compiler's channel for the `melody` role

# ---------------------------------------------------------------------------------------------------------------
# THRESH: the only place for numbers. TEMPORARY values (정의서 2절): no literature source, not calibrated.
# Do not use as a quality criterion or sign-off before the listening correlation check.
# ---------------------------------------------------------------------------------------------------------------
THRESH: dict[str, Any] = {
    # M1 melody repetition / variation
    "m1_min_bars": 4, "m1_w_rhythm": 0.5, "m1_w_interval": 0.5,
    "m1_exact": 0.9999, "m1_repeat": 0.85, "m1_variation": 0.5,
    "m1_rep_range": (0.25, 0.75), "m1_exact_max": 0.5, "m1_var_min": 0.10,
    # M2 interval distribution
    "m2_max_gap_quarters": 2, "m2_min_pairs": 8, "m2_leap_min": 5, "m2_huge_min": 13, "m2_recovery_max_step": 2, "m2_min_leaps_for_recovery": 3,
    "m2_step_range": (0.35, 0.80), "m2_leap_max": 0.30, "m2_huge_max": 0.05, "m2_repeat_max": 0.25, "m2_recovery_min": 0.5,
    # M3 contour diversity
    "m3_window_bars": 2, "m3_min_notes": 4, "m3_min_windows": 4, "m3_flat_span": 4, "m3_arch_drop": 2, "m3_arch_pos": (0.2, 0.8), "m3_asc_desc": 3,
    "m3_diversity_min": 0.40, "m3_adjacent_same_max": 0.60,
    # M4 final note
    "m4_section_end_stable_min": 0.60,
    # M5 sections
    "m5_contrast_dens": 0.15, "m5_contrast_reg": 2, "m5_contrast_vel": 8, "m5_return_dens": 0.25, "m5_return_reg": 3,
    # M6 chord movement
    "m6_min_chords": 3, "m6_move_range": (0.5, 3.0), "m6_voicing_max": 4.0,
    # M7 off-scale
    "m7_off_all_max": 0.10, "m7_off_strong_max": 0.05,
}

# mode -> scale intervals. The harmonic_minor row is ours (정의서 table lacks it; confirmed by the spec: {0,2,3,5,7,8,11}).
MODE_SCALE: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11), "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10), "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10), "phrygian": (0, 1, 3, 5, 7, 8, 10), "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10), "locrian": (0, 1, 3, 5, 6, 8, 10), "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}
MINOR_THIRD_MODES = {"minor", "aeolian", "dorian", "phrygian", "locrian", "harmonic_minor"}  # triad third = 3 semitones
CHORD_PCS: dict[str, tuple[int, ...]] = {  # 정의서 M6 table (same 10 qualities as the spec)
    "": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "m7": (0, 3, 7, 10), "maj7": (0, 4, 7, 11),
    "dim": (0, 3, 6), "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "m7b5": (0, 3, 6, 10),
}
CONTOUR_CLASSES = ("flat", "arch", "valley", "ascending", "descending", "wave")


@dataclass(frozen=True)
class Note:
    track: int
    channel: int
    start: int
    end: int
    pitch: int
    velocity: int


@dataclass
class ProxyResult:
    """One indicator: a verdict in {in_range, out_of_range, na}, the reason, and its named values (None = not measurable)."""

    status: str
    reason: str = ""
    values: dict[str, float | None] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)  # per-pair / per-slot records (M5, M6); written to proxy_details.jsonl, not to the CSV


def _r(x: float) -> float:
    return round(x, 4) + 0.0  # + 0.0 turns -0.0 into 0.0


def _na(reason: str, names: tuple[str, ...]) -> ProxyResult:
    return ProxyResult(NA, reason, {n: None for n in names})


# ----------------------------------------------------------------------------------------------------- material

@dataclass
class Material:
    notes: list[Note]            # without drum-channel notes
    tpq: int
    bt: int | None               # ticks per bar; None when the time signature changes (bar metrics -> na)
    mel: list[Note]              # skyline melody
    bass_last_pc: int | None
    tonic_pc: int | None
    mode: str | None
    sections: list[tuple[str, int, int]] | None   # (name, start_bar, end_bar exclusive), spec only
    chords: list[str] | None     # chord names in slot order, spec only
    chord_starts: list[int] | None   # tick where each chord slot starts (compiler layout), spec only
    chord_track_notes: list[Note] | None
    last_bar: int | None


def _read_notes(mid: mido.MidiFile) -> tuple[list[Note], set[tuple[int, int]]]:
    notes: list[Note] = []
    sigs: set[tuple[int, int]] = set()
    for ti, tr in enumerate(mid.tracks):
        t = 0
        open_: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                open_.setdefault((m.channel, m.note), []).append((t, m.velocity))
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                st = open_.get((m.channel, m.note))
                if st:
                    s, v = st.pop(0)
                    if m.channel != DRUM_CHANNEL:
                        notes.append(Note(ti, m.channel, s, t, m.note, v))
            elif m.type == "time_signature":
                sigs.add((m.numerator, m.denominator))
    notes.sort(key=lambda n: (n.start, n.pitch, n.track, n.end))
    return notes, sigs


def _skyline(notes: list[Note]) -> list[Note]:
    """Highest note per onset (ties: the longer one); ascending onset."""
    best: dict[int, Note] = {}
    for n in notes:
        b = best.get(n.start)
        if b is None or n.pitch > b.pitch or (n.pitch == b.pitch and n.end - n.start > b.end - b.start):
            best[n.start] = n
    return [best[s] for s in sorted(best)]


def _pick_track(notes: list[Note], highest: bool) -> list[Note]:
    """정의서 0.3 fallback: among tracks with >= 25% of the largest track's note count, the one with the highest
    (melody) or lowest (bass) median pitch; ties -> the smaller track number."""
    by: dict[int, list[Note]] = {}
    for n in notes:
        by.setdefault(n.track, []).append(n)
    if not by:
        return []
    top = max(len(v) for v in by.values())
    cands = [(median(x.pitch for x in v), t) for t, v in by.items() if len(v) >= 0.25 * top]
    pick = max(cands, key=lambda c: (c[0], -c[1])) if highest else min(cands, key=lambda c: (c[0], c[1]))
    return by[pick[1]]


def build_material(midi: bytes, spec: MusicSpec | None, expect=None) -> Material | None:
    try:
        mid = mido.MidiFile(file=io.BytesIO(midi))
    except Exception:  # noqa: BLE001
        return None
    notes, sigs = _read_notes(mid)
    tpq = mid.ticks_per_beat
    num, den = next(iter(sigs)) if len(sigs) == 1 else (4, 4)
    bt = None if len(sigs) >= 2 else max(1, tpq * 4 * num // den)
    melody_notes = None
    if spec is not None and any(t.role.value == "melody" for t in spec.tracks):
        melody_notes = [n for n in notes if n.channel == MELODY_CHANNEL]  # compiler convention: melody role = channel 0
    mel = _skyline(melody_notes if melody_notes is not None else _pick_track(notes, True))
    bass = _skyline(_pick_track(notes, False)) if notes else []
    tonic_pc = mode = None
    if spec is not None:
        tonic_pc, mode = PITCH_CLASS[spec.key.tonic.value], spec.key.mode.value
    elif expect is not None and expect.tonic is not None and expect.mode is not None:
        tonic_pc, mode = PITCH_CLASS[expect.tonic.value], expect.mode.value
    sections = chords = starts = chord_notes = None
    if spec is not None and bt is not None:
        sections, pos = [], 0
        chords, starts = [], []
        for s in spec.sections:
            sections.append((s.name, pos, pos + s.bars))
            n_slots = s.bars * s.chords_per_bar
            for i in range(n_slots):
                chords.append(str(s.chords[i % len(s.chords)]))
                starts.append(pos * bt + i * (s.bars * bt // n_slots))
            pos += s.bars
        roles = {t.role.value for t in spec.tracks}
        ch = 2 if "chords" in roles else 3 if "pad" in roles else None
        chord_notes = [n for n in notes if n.channel == ch] if ch is not None else None
    last_bar = ((max(n.end for n in notes) - 1) // bt) if notes and bt else None
    return Material(notes, tpq, bt, mel, bass[-1].pitch % 12 if bass else None, tonic_pc, mode, sections, chords, starts, chord_notes, last_bar)


# ------------------------------------------------------------------------------------------------------------ M1

M1_VALUES = ("rep_rate", "exact_rate", "var_rate", "new_rate")


def _ic(d: int) -> int:
    a = abs(d)
    c = 0 if a == 0 else 1 if a <= 2 else 2 if a <= 4 else 3 if a <= 7 else 4
    return c if d >= 0 else -c


def _lev(a: list[int], b: list[int]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def m1_melody_repetition(m: Material) -> ProxyResult:
    """M1: how much of the melody is restated (exact / near-copy) versus varied versus new, bar by bar.

    Stands in for: motif presentation, repetition and variation. Limits: a bar copied with one note changed again and
    again has a high `var_rate` and is still dull; a fine piece with little repetition is out of range; the 16th-note
    grid ignores triplets and swing; the interval classes treat scale-step moves as equal. TEMPORARY thresholds (THRESH).
    """
    T = THRESH
    if m.bt is None:
        return _na("박자표가 곡 중간에 바뀜", M1_VALUES)
    bars: dict[int, list[Note]] = {}
    for n in m.mel:
        bars.setdefault(n.start // m.bt, []).append(n)
    V = sorted(bars)
    if len(V) < T["m1_min_bars"]:
        return _na(f"선율이 있는 마디 {len(V)}개 < {T['m1_min_bars']}", M1_VALUES)
    gs = m.tpq / 4
    sig = {}
    for b in V:
        ns = bars[b]
        R = {round((n.start - b * m.bt) / gs) for n in ns}
        I = [_ic(y.pitch - x.pitch) for x, y in zip(ns, ns[1:])]
        sig[b] = (R, I)

    def sim(a: int, b: int) -> float:
        (Ra, Ia), (Rb, Ib) = sig[a], sig[b]
        j = len(Ra & Rb) / len(Ra | Rb)
        s = 1.0 if not Ia and not Ib else 0.0 if not Ia or not Ib else 1 - _lev(Ia, Ib) / max(len(Ia), len(Ib))
        return T["m1_w_rhythm"] * j + T["m1_w_interval"] * s

    exact = rep = var = new = 0
    for k, i in enumerate(V[1:], 1):
        s = max(sim(i, j) for j in V[:k])
        if s >= T["m1_exact"]:
            exact += 1
        elif s >= T["m1_repeat"]:
            rep += 1
        elif s >= T["m1_variation"]:
            var += 1
        else:
            new += 1
    n = len(V) - 1
    v = {"rep_rate": _r((exact + rep) / n), "exact_rate": _r(exact / n), "var_rate": _r(var / n), "new_rate": _r(new / n)}
    lo, hi = T["m1_rep_range"]
    bad = [name for name, ok in (("rep_rate", lo <= v["rep_rate"] <= hi), ("exact_rate", v["exact_rate"] <= T["m1_exact_max"]), ("var_rate", v["var_rate"] >= T["m1_var_min"])) if not ok]
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v)


# ------------------------------------------------------------------------------------------------------------ M2

M2_VALUES = ("step_share", "leap_share", "huge_share", "repeat_share", "skip_share", "recovery")


def m2_interval_distribution(m: Material) -> ProxyResult:
    """M2: share of repeated notes, steps, thirds, leaps and over-octave leaps between successive melody notes, plus how often
    a leap is followed by a step back the other way.

    Stands in for: smoothness and singability of the line. Limits: a random walk of steps is in range; direction, rhythm and
    harmony fit are not looked at; a wrongly extracted melody track distorts it; some genres legitimately leap more.
    TEMPORARY thresholds (THRESH).
    정의서 질문 4: "the next pair" of a leap is read as the pair right after it in the melody; a pair dropped for a long rest counts as no follower.
    """
    T = THRESH
    mel = m.mel
    pairs: list[int | None] = []  # signed interval of pair k=(mel[k],mel[k+1]) or None if the rest is longer than the limit
    for a, b in zip(mel, mel[1:]):
        pairs.append(b.pitch - a.pitch if b.start - a.end <= T["m2_max_gap_quarters"] * m.tpq else None)
    kept = [d for d in pairs if d is not None]
    if len(kept) < T["m2_min_pairs"]:
        return _na(f"프레이즈 안 음정 쌍 {len(kept)}개 < {T['m2_min_pairs']}", M2_VALUES)
    n = len(kept)
    ab = [abs(d) for d in kept]
    v: dict[str, float | None] = {
        "repeat_share": _r(sum(d == 0 for d in ab) / n), "step_share": _r(sum(1 <= d <= 2 for d in ab) / n),
        "skip_share": _r(sum(3 <= d <= 4 for d in ab) / n), "leap_share": _r(sum(d >= T["m2_leap_min"] for d in ab) / n),
        "huge_share": _r(sum(d >= T["m2_huge_min"] for d in ab) / n),
    }
    leaps = [k for k, d in enumerate(pairs) if d is not None and abs(d) >= T["m2_leap_min"]]
    v["recovery"] = None
    if len(leaps) >= T["m2_min_leaps_for_recovery"]:
        have = [k for k in leaps if k + 1 < len(pairs) and pairs[k + 1] is not None]
        if have:
            ok = sum(1 for k in have if pairs[k] * pairs[k + 1] < 0 and abs(pairs[k + 1]) <= T["m2_recovery_max_step"])
            v["recovery"] = _r(ok / len(have))
    lo, hi = T["m2_step_range"]
    checks = [("step_share", lo <= v["step_share"] <= hi), ("leap_share", v["leap_share"] <= T["m2_leap_max"]),
              ("huge_share", v["huge_share"] <= T["m2_huge_max"]), ("repeat_share", v["repeat_share"] <= T["m2_repeat_max"])]
    if v["recovery"] is not None:
        checks.append(("recovery", v["recovery"] >= T["m2_recovery_min"]))
    bad = [name for name, ok in checks if not ok]
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v)


# ------------------------------------------------------------------------------------------------------------ M3

M3_VALUES = ("diversity", "adjacent_same")


def contour_class(p: list[int]) -> str:
    T = THRESH
    n = len(p)
    hi, lo, a, z = max(p), min(p), p[0], p[-1]
    thi, tlo = p.index(hi) / (n - 1), p.index(lo) / (n - 1)
    if hi - lo <= T["m3_flat_span"]:
        return "flat"
    if hi - max(a, z) >= T["m3_arch_drop"] and T["m3_arch_pos"][0] <= thi <= T["m3_arch_pos"][1]:
        return "arch"
    if min(a, z) - lo >= T["m3_arch_drop"] and T["m3_arch_pos"][0] <= tlo <= T["m3_arch_pos"][1]:
        return "valley"
    if z - a >= T["m3_asc_desc"]:
        return "ascending"
    if a - z >= T["m3_asc_desc"]:
        return "descending"
    return "wave"


def m3_contour_diversity(m: Material) -> ProxyResult:
    """M3: entropy of the shapes (flat/arch/valley/ascending/descending/wave) of 2-bar windows, and how often neighbouring
    windows share a shape.

    Stands in for: variety of phrase shapes. Limits: six coarse shapes (two different tunes can both be "arch"); fixed 2-bar
    windows do not match real phrases; with 12 bars there are only 6 windows (at least 4 are required). TEMPORARY thresholds.
    정의서 질문 3: "neighbouring windows" are neighbours in the list of windows that have >= 4 notes (skipped windows do not break adjacency).
    """
    T = THRESH
    if m.bt is None:
        return _na("박자표가 곡 중간에 바뀜", M3_VALUES)
    wins: dict[int, list[int]] = {}
    for n in m.mel:
        wins.setdefault((n.start // m.bt) // T["m3_window_bars"], []).append(n.pitch)
    shapes = [contour_class(p) for _w, p in sorted(wins.items()) if len(p) >= T["m3_min_notes"]]
    if len(shapes) < T["m3_min_windows"]:
        return _na(f"4개 이상의 음이 있는 {T['m3_window_bars']}마디 창 {len(shapes)}개 < {T['m3_min_windows']}", M3_VALUES)
    q = [shapes.count(c) / len(shapes) for c in CONTOUR_CLASSES]
    h = -sum(x * math.log2(x) for x in q if x > 0)
    v = {"diversity": _r(h / math.log2(len(CONTOUR_CLASSES))), "adjacent_same": _r(sum(a == b for a, b in zip(shapes, shapes[1:])) / (len(shapes) - 1))}
    bad = [n for n, ok in (("diversity", v["diversity"] >= T["m3_diversity_min"]), ("adjacent_same", v["adjacent_same"] <= T["m3_adjacent_same_max"])) if not ok]
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v)


# ------------------------------------------------------------------------------------------------------------ M4

M4_VALUES = ("final_is_tonic", "final_arrived", "final_score", "bass_final_tonic", "section_end_stable")


def _triad(tonic: int, mode: str) -> set[int]:
    third = 3 if mode in MINOR_THIRD_MODES else 4
    fifth = 6 if mode == "locrian" else 7
    return {tonic % 12, (tonic + third) % 12, (tonic + fifth) % 12}


def m4_final_note(m: Material) -> ProxyResult:
    """M4: does the melody end on the tonic (in the last bar, long or on the downbeat), and do section ends land on a triad tone.

    Stands in for: cadential closure. Limits: only one necessary condition of a cadence (no V-I, no metric feel); a deliberate
    open ending is out of range; the key is the DECLARED one (never estimated), so our own output scores high by construction.
    TEMPORARY thresholds.
    정의서 질문 5: "the last melody note before the section end" = the last melody note that starts before the section's end bar; sections with no earlier melody note are skipped.
    정의서 질문 6: the key is the spec's; if there is no spec, the evaluation set's tonic+mode label is used (정의서 6절 says external MIDI needs a label).
    """
    T = THRESH
    if m.tonic_pc is None or m.mode is None:
        return _na("조(tonic·mode)를 알 수 없음", M4_VALUES)
    if m.mode not in MODE_SCALE:
        return _na(f"모드 {m.mode!r} 가 표에 없음", M4_VALUES)
    if m.bt is None or m.last_bar is None:
        return _na("박자표가 곡 중간에 바뀜 또는 음표 없음", M4_VALUES)
    if not m.mel:
        return _na("선율이 없음", M4_VALUES)
    last = m.mel[-1]
    pc = last.pitch % 12
    triad = _triad(m.tonic_pc, m.mode)
    is_tonic = pc == m.tonic_pc
    arrived = is_tonic and last.start // m.bt == m.last_bar and (last.end - last.start >= m.tpq or last.start % m.bt == 0)
    v: dict[str, float | None] = {
        "final_is_tonic": 1.0 if is_tonic else 0.0, "final_arrived": 1.0 if arrived else 0.0,
        "final_score": 1.0 if is_tonic else 0.5 if pc in triad else 0.0,
        "bass_final_tonic": None if m.bass_last_pc is None else (1.0 if m.bass_last_pc == m.tonic_pc else 0.0), "section_end_stable": None,
    }
    if m.sections and len(m.sections) >= 2:
        stable = total = 0
        for _name, _b0, b1 in m.sections[:-1]:
            before = [n for n in m.mel if n.start < b1 * m.bt]
            if before:
                total += 1
                stable += before[-1].pitch % 12 in triad
        v["section_end_stable"] = _r(stable / total) if total else None
    bad = []
    if v["final_arrived"] != 1.0:
        bad.append("final_arrived")
    if v["section_end_stable"] is not None and v["section_end_stable"] < T["m4_section_end_stable_min"]:
        bad.append("section_end_stable")
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v)


# ------------------------------------------------------------------------------------------------------------ M5

M5_VALUES = ("d_dens", "d_reg", "d_range", "d_vel", "contrast_present", "return_close")


def _nearest_rank(sorted_vals: list[int], q: float) -> int:
    return sorted_vals[max(0, math.ceil(q * len(sorted_vals)) - 1)]


def return_pairs(names: list[str]) -> list[tuple[int, int]]:
    """Section index pairs (earlier, later) where the later name is an earlier section's name plus digits (A -> A2).
    ASSUMPTION (정의서 질문 2): the 정의서 says the spec marks repeats; our MusicSpec has no such field, so the A/A2 naming of
    the expander is used."""
    out = []
    for j, nm in enumerate(names):
        stem = nm.rstrip("0123456789")
        if stem != nm and stem in names[:j]:
            out.append((names.index(stem), j))
    return out


def m5_section_contrast(m: Material) -> ProxyResult:
    """M5: do neighbouring sections differ in density / register / loudness, and does a returning section (A2) come back close to A.

    **Reporting rule (연구 1팀 허지민 제안): the CSV carries, for each d_* value, the LARGEST over the neighbouring section pairs; the
    value of every pair (neighbouring and return pairs, with its kind and the four d_* values) is in `proxy_details.jsonl`.**
    Stands in for: arrangement contrast and the consistency of a reprise. Limits: says "different", not how or whether it sounds
    good; ignores drums; very short sections give unstable medians. TEMPORARY thresholds.
    """
    T = THRESH
    if not m.sections or len(m.sections) < 2 or m.bt is None:
        return _na("섹션 정보가 없거나 섹션이 2개 미만(외부 MIDI는 임의 분할하지 않음)", M5_VALUES)
    stats = []
    for name, b0, b1 in m.sections:
        lo, hi = b0 * m.bt, b1 * m.bt
        inside = [n for n in m.notes if lo <= n.start < hi]
        mp = sorted(n.pitch for n in m.mel if lo <= n.start < hi)
        if not mp:
            return _na(f"섹션 {name} 에 선율 음이 없음", M5_VALUES)
        stats.append({"dens": len(inside) / (b1 - b0), "med": _nearest_rank(mp, 0.5), "rng": _nearest_rank(mp, 0.9) - _nearest_rank(mp, 0.1),
                      "vel": sum(n.velocity for n in inside) / len(inside) if inside else 0.0})

    def diff(x, y):
        mx = max(x["dens"], y["dens"])
        return (0.0 if mx == 0 else abs(x["dens"] - y["dens"]) / mx, abs(x["med"] - y["med"]), abs(x["rng"] - y["rng"]), abs(x["vel"] - y["vel"]))

    adj = [diff(a, b) for a, b in zip(stats, stats[1:])]
    names = [sec[0] for sec in m.sections]
    contrast = any(d[0] >= T["m5_contrast_dens"] or d[1] >= T["m5_contrast_reg"] or d[3] >= T["m5_contrast_vel"] for d in adj)
    rets = return_pairs([s[0] for s in m.sections])
    ret_close = None
    if rets:
        ret_close = all(diff(stats[a], stats[b])[0] <= T["m5_return_dens"] and diff(stats[a], stats[b])[1] <= T["m5_return_reg"] for a, b in rets)
    v: dict[str, float | None] = {"d_dens": _r(max(d[0] for d in adj)), "d_reg": _r(max(d[1] for d in adj)), "d_range": _r(max(d[2] for d in adj)),
                                  "d_vel": _r(max(d[3] for d in adj)), "contrast_present": 1.0 if contrast else 0.0,
                                  "return_close": None if ret_close is None else (1.0 if ret_close else 0.0)}
    bad = [n for n, ok in (("contrast_present", contrast), ("return_close", ret_close is None or ret_close)) if not ok]

    def pair(kind, a, b, d):
        rec = {"kind": kind, "a": names[a], "b": names[b], "d_dens": _r(d[0]), "d_reg": _r(d[1]), "d_range": _r(d[2]), "d_vel": _r(d[3])}
        if kind == "adjacent":   # the contrast test of this pair
            rec["contrast"] = d[0] >= T["m5_contrast_dens"] or d[1] >= T["m5_contrast_reg"] or d[3] >= T["m5_contrast_vel"]
        else:                    # the return test of this pair
            rec["close"] = d[0] <= T["m5_return_dens"] and d[1] <= T["m5_return_reg"]
        return rec

    details = {"sections": [{"name": sec[0], "start_bar": sec[1], "end_bar": sec[2], "density": _r(st["dens"]), "median_pitch": st["med"], "range_p90_p10": st["rng"], "mean_velocity": _r(st["vel"])}
                            for sec, st in zip(m.sections, stats)],
               "pairs": [pair("adjacent", i, i + 1, d) for i, d in enumerate(adj)] + [pair("return", a, b, diff(stats[a], stats[b])) for a, b in rets]}
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v, details)


# ------------------------------------------------------------------------------------------------------------ M6

M6_VALUES = ("chord_move_pc", "chord_change_rate", "voicing_move")


def chord_pcs(name: str) -> tuple[int, ...] | None:
    try:
        root, quality = parse_chord(name)
    except Exception:  # noqa: BLE001
        return None
    offs = CHORD_PCS.get(quality)
    return None if offs is None else tuple(sorted({(root + o) % 12 for o in offs}))


def _move(x: list[int], y: list[int], dist) -> float:
    """정의서 M6 step 2: pad the smaller set (ascending, cycled) to k = max size, best one-to-one matching, mean per voice."""
    k = max(len(x), len(y))

    def pad(s):
        s = sorted(s)
        return [s[i % len(s)] for i in range(k)]

    a, b = pad(x), pad(y)
    return min(sum(dist(a[i], p[i]) for i in range(k)) for p in permutations(b)) / k


def _circ(a: int, b: int) -> int:
    d = abs(a - b) % 12
    return min(d, 12 - d)


def m6_chord_movement(m: Material) -> ProxyResult:
    """M6: mean semitone movement per voice between neighbouring chords (pitch-class sets), the share of changing chords, and the
    movement of the actual voicing in the chord track.

    Stands in for: smoothness of the harmonic progression (voice leading). Limits: small movement is not good harmony (a static
    chord is smooth and boring, hence no change = out of range); no function (why I-V-vi-IV is common); style dependent; a chord
    track that re-strikes root position raises `voicing_move` without being bad. Chord names come from the spec only. TEMPORARY thresholds.
    The CSV carries the MEANS over all chord changes; every slot-to-slot move (chord names, pitch-class move, whether the chord changed,
    the realised voicing move) is in `proxy_details.jsonl`.
    정의서 질문 7: `chord_change_rate` compares chord NAMES (not pitch-class sets). 질문 8: the chord list is the compiler's slot order over all sections (chords cycled per section, chords_per_bar slots per bar), pairs across a section border included. 질문 9: the voicing at a slot is the set of chord-track notes sounding at the slot's start tick; the chord track is the spec's `chords` role (channel 2), else `pad` (channel 3).
    """
    T = THRESH
    if m.chords is None or m.bt is None:
        return _na("사양의 코드 이름이 없음(외부 MIDI는 코드를 추정하지 않음) 또는 박자표 변경", M6_VALUES)
    if len(m.chords) < T["m6_min_chords"]:
        return _na(f"코드 {len(m.chords)}개 < {T['m6_min_chords']}", M6_VALUES)
    sets = [chord_pcs(c) for c in m.chords]
    if any(s is None for s in sets):
        return _na("코드 음질이 표 밖", M6_VALUES)
    moves = [_move(list(a), list(b), _circ) for a, b in zip(sets, sets[1:])]
    changed = sum(a != b for a, b in zip(m.chords, m.chords[1:]))
    v: dict[str, float | None] = {"chord_move_pc": _r(sum(moves) / len(moves)), "chord_change_rate": _r(changed / len(moves)), "voicing_move": None}
    per_voicing: list[float | None] = [None] * len(moves)
    if m.chord_track_notes:
        sounding = [sorted(n.pitch for n in m.chord_track_notes if n.start <= t < n.end) for t in m.chord_starts]
        per_voicing = [_move(a, b, lambda p, q: abs(p - q)) if a and b else None for a, b in zip(sounding, sounding[1:])]
        dists = [d for d in per_voicing if d is not None]
        v["voicing_move"] = _r(sum(dists) / len(dists)) if dists else None
    details = {"moves": [{"slot": i, "from": m.chords[i], "to": m.chords[i + 1], "start_tick": m.chord_starts[i], "move_pc": _r(mv), "changed": m.chords[i] != m.chords[i + 1],
                          "voicing_move": None if pv is None else _r(pv)} for i, (mv, pv) in enumerate(zip(moves, per_voicing))]}
    lo, hi = T["m6_move_range"]
    bad = []
    if v["chord_change_rate"] == 0.0 or not lo <= v["chord_move_pc"] <= hi:
        bad.append("chord_move_pc" if v["chord_change_rate"] else "chord_change_rate")
    if v["voicing_move"] is not None and v["voicing_move"] > T["m6_voicing_max"]:
        bad.append("voicing_move")
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v, details)


# ------------------------------------------------------------------------------------------------------------ M7

M7_VALUES = ("off_all", "off_strong", "off_dur_all_tracks")


def m7_off_scale(m: Material) -> ProxyResult:
    """M7: share of melody notes outside the declared scale (all, and only the strong ones: on a quarter-note position or >= 1 beat
    long), and the duration-weighted share over all tracks.

    Stands in for: staying inside the key. Limits: chromatic passing tones, borrowed chords and blues scales are normal but count
    as deviations (no genre relaxation here); for our own output with a declared key it is near 0 BY CONSTRUCTION, so it is a
    regression check that the engine keeps to the scale, not evidence of musicality. Does NOT replace `scale_deviation`.
    TEMPORARY thresholds.
    """
    T = THRESH
    if m.tonic_pc is None or m.mode is None:
        return _na("조(tonic·mode)를 알 수 없음", M7_VALUES)
    if m.mode not in MODE_SCALE:
        return _na(f"모드 {m.mode!r} 가 표에 없음", M7_VALUES)
    if not m.mel:
        return _na("선율이 없음", M7_VALUES)
    scale = {(m.tonic_pc + x) % 12 for x in MODE_SCALE[m.mode]}
    off = lambda n: n.pitch % 12 not in scale  # noqa: E731
    strong = [n for n in m.mel if n.start % m.tpq == 0 or n.end - n.start >= m.tpq]
    total_dur = sum(n.end - n.start for n in m.notes)
    v: dict[str, float | None] = {
        "off_all": _r(sum(off(n) for n in m.mel) / len(m.mel)),
        "off_strong": _r(sum(off(n) for n in strong) / len(strong)) if strong else None,
        "off_dur_all_tracks": _r(sum(n.end - n.start for n in m.notes if off(n)) / total_dur) if total_dur else None,
    }
    bad = []
    if v["off_all"] > T["m7_off_all_max"]:
        bad.append("off_all")
    if v["off_strong"] is not None and v["off_strong"] > T["m7_off_strong_max"]:
        bad.append("off_strong")
    return ProxyResult(OUT_OF_RANGE if bad else IN_RANGE, ("범위 밖: " + ", ".join(bad)) if bad else "모두 범위 안", v)


# --------------------------------------------------------------------------------------------------------- driver

PROXIES = (
    ("m1", "M1 선율 반복률·변주", m1_melody_repetition, M1_VALUES),
    ("m2", "M2 음정 분포", m2_interval_distribution, M2_VALUES),
    ("m3", "M3 윤곽 다양성", m3_contour_diversity, M3_VALUES),
    ("m4", "M4 종지음 도착", m4_final_note, M4_VALUES),
    ("m5", "M5 섹션 간 밀도·음역 차이", m5_section_contrast, M5_VALUES),
    ("m6", "M6 코드 간 평균 이동 거리", m6_chord_movement, M6_VALUES),
    ("m7", "M7 음계 이탈률", m7_off_scale, M7_VALUES),
)
COLUMN_PREFIX = "proxy_"


def evaluate(midi: bytes | None, spec: MusicSpec | None = None, expect=None, valid: bool = True) -> dict[str, ProxyResult]:
    """{m1..m7: ProxyResult}. `valid=False` (midicheck_valid or note_integrity failed, or nothing was generated) -> all na."""
    if midi is None or not valid:
        return {k: _na("유효성 실패" if midi is not None else "생성 실패", names) for k, _t, _f, names in PROXIES}
    mat = build_material(midi, spec, expect)
    if mat is None:
        return {k: _na("MIDI 를 읽을 수 없음", names) for k, _t, _f, names in PROXIES}
    return {k: fn(mat) for k, _t, fn, _n in PROXIES}


def column_names() -> list[str]:
    cols: list[str] = []
    for k, _t, _f, names in PROXIES:
        cols += [COLUMN_PREFIX + k, f"{COLUMN_PREFIX}{k}_reason"] + [f"{COLUMN_PREFIX}{k}_{n}" for n in names]
    return cols
