"""Compiler v2 melody (docs/compiler-v2.md §4.1-4.3): phrases (presentation / development / cadence), motif
development (repeat, sequence, variation) and chord-tone correction with passing / neighbour tones.

Pure and deterministic: all randomness comes from the compiler's own `Rng` with one label per stage
("melody.section", "melody.develop", "melody.vary", "melody.vel"), so a different number of draws in one stage never
shifts another. No external data; everything here is general music theory plus constants written for this project.

Pitch is handled as a *scale-step index* (0 = tonic of the motif's base octave, 7 steps per octave, negative = below),
so sequences and inversions stay inside the key; chord-tone correction is the only step that leaves the scale.
Nothing here is evidence of musical quality - see docs/compiler-v2.md §7.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Callable

from ..music import degree_offset, effective_register, motif_pitches
from ..spec import MODE_INTERVALS, RHYTHM_BEATS, MusicSpec, Track, is_rest, parse_chord
from ..validate.theory import CHORD_TONES
from . import drums as D
from .rng import Rng

PHRASE_BARS = 4
SEQ_STEPS = (1, -1, 2)      # sequence transpositions, in scale steps
SEQ_CUM_MAX = 3             # a phrase never drifts more than this many scale steps from its start
SECTION_SHIFTS = (0, 0, 1, -1, 2)
MAX_PASSTHROUGH = 2         # a passing / neighbour tone moves at most 2 semitones from its neighbours
VARIATIONS = ("last_note", "invert", "shorten")


@dataclass
class V2Note:
    start: int
    end: int
    pitch: int
    velocity: int
    phrase: int      # running phrase number
    pass_k: int      # motif pass inside the phrase
    op: str          # orig | seq | var:<kind> | seq+var
    motif_i: int     # index into motif.degrees (or -1 for a merged note)


def phrase_lengths(bars: int) -> list[int]:
    """Phrase lengths in bars: 4-bar phrases; a remainder of 1 joins the last phrase, 2-3 bars form a short phrase."""
    n, rem = divmod(bars, PHRASE_BARS)
    if n == 0:
        return [bars]
    out = [PHRASE_BARS] * n
    if rem == 1:
        out[-1] += 1
    elif rem:
        out.append(rem)
    return out


def phrase_role(index: int, count: int) -> str:
    if index == 0:
        return "presentation"
    return "cadence" if index == count - 1 else "development"


def nonchord_role(prev: int, n: int, nxt: int) -> str | None:
    """Classify a non-chord tone between two neighbours: 'passing' (stepwise, same direction), 'neighbor'
    (step away and back) or None (neither: it must be moved onto a chord tone)."""
    d1, d2 = n - prev, nxt - n
    if d1 and d2 and (d1 > 0) == (d2 > 0) and abs(d1) <= MAX_PASSTHROUGH and abs(d2) <= MAX_PASSTHROUGH:
        return "passing"
    if prev == nxt and 1 <= abs(d1) <= MAX_PASSTHROUGH:
        return "neighbor"
    return None


def chord_tones(chord: str) -> frozenset[int]:
    root, quality = parse_chord(chord)
    return frozenset((root + i) % 12 for i in CHORD_TONES[quality])


def _snap(pitch: int, tones: frozenset[int], lo: int, hi: int, prev: int | None) -> int:
    """Nearest chord tone inside [lo, hi]; ties go to the one closer to the previous note, then upward."""
    best = None
    for p in range(lo, hi + 1):
        if p % 12 in tones:
            key = (abs(p - pitch), abs(p - prev) if prev is not None else 0, -p)
            if best is None or key < best[0]:
                best = (key, p)
    return best[1] if best else pitch


def _variation(kind: str, degs: list[int], complete: bool, rng: Rng) -> list[int]:
    if kind == "invert" and degs:
        return [2 * degs[0] - d for d in degs]
    if kind == "last_note" and complete and len(degs) >= 2:
        out = list(degs)
        out[-1] += rng.choice([1, -1, 2])
        return out
    return list(degs)


@dataclass
class _Ev:
    t: int
    length: int
    midx: int | None     # None = rest
    deg: int = 0
    phrase: int = 0
    pass_k: int = 0
    op: str = "orig"
    sec: int = 0


def build_melody(
    spec: MusicSpec,
    track: Track,
    seed: int,
    slots: list[tuple[int, int, str, int, object]],
    bar: int,
    ppq: int,
    velocity: Callable[[float, Rng], int],
) -> list[V2Note] | None:
    """v2 melody notes, or None when the motif does not fit the register (the caller falls back to v1)."""
    motif, reg = track.motif, effective_register(track)
    if motif is None or reg is None:
        return None
    ref = motif_pitches(spec, track, 0)
    if ref is None:
        return None
    lo, hi = reg
    iv = MODE_INTERVALS[spec.key.mode.value]
    base = ref[0] - degree_offset(spec, motif.degrees[0])   # pitch of scale step 0 (the tonic)
    idx_of = [d - 1 if d > 0 else d for d in motif.degrees]  # degree -> continuous scale-step index

    def pitch(i: int) -> int:
        return base + 12 * (i // 7) + iv[i % 7]

    rng_sec, rng_dev = Rng(seed, "melody.section"), Rng(seed, "melody.develop")
    rng_var, rng_vel = Rng(seed, "melody.vary"), Rng(seed, "melody.vel")
    rhythm = [r.value for r in motif.rhythm]
    n_notes = len(motif.degrees)

    events: list[_Ev] = []
    sec_pos, phrase_no = 0, 0
    for si, sec in enumerate(spec.sections):
        shift = 0 if si == 0 else rng_sec.choice(list(SECTION_SHIFTS))
        lengths = phrase_lengths(sec.bars)
        p_start = sec_pos
        for pi, plen in enumerate(lengths):
            p_end = p_start + plen * bar
            role = phrase_role(pi, len(lengths)) if len(lengths) > 1 else "presentation"
            cum, last_dir, run = 0, 0, 0
            t, ri = p_start, 0
            passes: dict[int, list[_Ev]] = {}
            while t < p_end:
                k = ri // len(rhythm)
                sym = rhythm[ri % len(rhythm)]
                ln = min(int(RHYTHM_BEATS[sym] * ppq), p_end - t)
                ev = _Ev(t, ln, None, 0, phrase_no, k, "orig", si)
                if not is_rest(sym):
                    ev.midx = sum(1 for s in rhythm[: ri % len(rhythm)] if not is_rest(s))
                passes.setdefault(k, []).append(ev)
                events.append(ev)
                t += ln
                ri += 1
            for k in sorted(passes):
                evs = passes[k]
                notes = [e for e in evs if e.midx is not None]
                complete = len(notes) == n_notes
                degs = [idx_of[e.midx] + shift for e in notes]
                offset_in_phrase = evs[0].t - p_start
                ops: list[str] = []
                sequence, fixed_step, var_kind = False, 0, None
                if role == "presentation":
                    sequence = k >= 2                       # statement, repeat, then sequence
                elif role == "development":
                    if k % 2 == 0:
                        sequence = True
                    else:
                        var_kind = VARIATIONS[rng_var.int(0, len(VARIATIONS) - 1)]
                else:                                       # cadence: restate the opening bars, then lead down
                    if offset_in_phrase >= 2 * bar:
                        fixed_step, var_kind = -1, "last_note"
                if sequence:
                    step = rng_dev.choice(list(SEQ_STEPS))
                    if abs(cum + step) > SEQ_CUM_MAX or (run >= 2 and (step > 0) == (last_dir > 0)):
                        step = -step
                    run = run + 1 if last_dir and (step > 0) == (last_dir > 0) else 1
                    last_dir, cum = step, cum + step
                    ops.append("seq")
                elif fixed_step:
                    cum += fixed_step
                    ops.append("seq")
                degs = [d + cum for d in degs]
                if var_kind:
                    degs = _variation(var_kind, degs, complete, rng_var)
                    ops.append(f"var:{var_kind}")
                    if var_kind == "shorten" and complete and len(notes) >= 3:
                        last = notes[-1]            # the last two notes become one (same time span)
                        notes[-2].length += last.length
                        last.midx = None
                        degs, notes = degs[:-1], notes[:-1]
                for e, d in zip(notes, degs):
                    e.deg = d
                    e.op = "+".join(ops) if ops else "orig"
            phrase_no += 1
            p_start = p_end
        sec_pos = p_start

    # events -> pitches (fold into the register), keeping rests and absorbed notes out of the line
    line = [e for e in events if e.midx is not None]
    pitches: list[int] = []
    for e in line:
        i = e.deg
        while pitch(i) > hi:
            i -= 7
        while pitch(i) < lo:
            i += 7
        pitches.append(pitch(i))

    starts = [s[0] for s in slots]

    def slot_of(t: int) -> int:
        return bisect.bisect_right(starts, t) - 1

    kicks = D.GRID[spec.time_signature][1]
    strong_pos = {k * (ppq // 2) for k in kicks}

    def is_strong(t: int) -> bool:
        return t % bar in strong_pos or t == starts[slot_of(t)]

    tones = [chord_tones(slots[slot_of(e.t)][2]) for e in line]
    final = list(pitches)
    # 1) strong beats and chord changes must be chord tones
    prev: int | None = None
    for j, e in enumerate(line):
        if is_strong(e.t) and final[j] % 12 not in tones[j]:
            final[j] = _snap(final[j], tones[j], lo, hi, prev)
        prev = final[j]
    # 2) weak non-chord tones survive only as passing / neighbour tones between chord tones
    for j, e in enumerate(line):
        if final[j] % 12 in tones[j]:
            continue
        p_ = final[j - 1] if j > 0 else None
        n_ = final[j + 1] if j + 1 < len(line) else None
        # a not-yet-visited weak neighbour still holds its raw pitch, so "chord tone" is judged on `final` throughout
        ok = (p_ is not None and n_ is not None and p_ % 12 in tones[j - 1] and n_ % 12 in tones[j + 1]
              and nonchord_role(p_, final[j], n_) is not None)
        if not ok:
            final[j] = _snap(final[j], tones[j], lo, hi, p_)

    out: list[V2Note] = []
    for e, p in zip(line, final):
        sec = spec.sections[e.sec]
        out.append(V2Note(e.t, e.t + max(1, int(e.length * 0.92)), p, velocity(sec.energy, rng_vel), e.phrase, e.pass_k, e.op, e.midx))
    return out
