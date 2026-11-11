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

# --- Constants that only listening can settle (all UNVERIFIED by ear: initial values, collected here on purpose) ---
BREATH_BEATS = 0.5          # silence before a phrase end / the 3rd bar of a long phrase (docs/compiler-v2.md §4.6)
MIN_NOTE_BEATS = 0.25       # a note is never shortened below this
APEX_POSITION = 2 / 3       # where in the phrase the single highest note should sit (§4.4)
CENTER_BASE, CENTER_ENERGY = 0.35, 0.4   # register centre = lo + (hi - lo) * (CENTER_BASE + CENTER_ENERGY * energy)
CENTER_SHIFT_MAX = 2        # a section moves at most this many scale steps toward its register centre
BREATH_ENERGY_MAX = 0.5     # the extra mid-phrase breath (end of bar 3) only below this section energy
# --- Rule constants (music theory) and tuning constants (chosen to trade leaps against motif fidelity) ---
LEAP_MIN = 5                # semitones: a leap (a fourth or more) is followed by a move in the opposite direction
LEAP_MAX = 12               # no leap beyond an octave
LEAP_COST_FREE = 4          # snapping to a chord tone costs extra for every semitone of leap beyond this
LEAP_COST_WEIGHT = 2
CONTOUR_COST = 6           # snapping away from the motif's direction costs this many semitones

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


def _snap(pitch: int, tones: frozenset[int], lo: int, hi: int, prev: int | None, want: int = 0) -> int:
    """Chord tone inside [lo, hi] closest to `pitch`, with a penalty for leaping away from the previous note
    (so snapping does not manufacture leaps); ties go to the one closer to the previous note, then upward."""
    best = None
    for p in range(lo, hi + 1):
        if p % 12 in tones:
            leap = max(0, abs(p - prev) - LEAP_COST_FREE) if prev is not None else 0
            cost = abs(p - pitch) + LEAP_COST_WEIGHT * leap
            if prev is not None and want:   # keep the motif's direction (and avoid creating a repeated note)
                step = (p > prev) - (p < prev)
                cost += CONTOUR_COST * (step != want)
            key = (cost, abs(p - prev) if prev is not None else 0, -p)
            if best is None or key < best[0]:
                best = (key, p)
    return best[1] if best else pitch


@dataclass(frozen=True)
class _ChordInfo:
    root: int
    third: int | None
    fifth: int | None
    tones: frozenset[int]


def chord_tones_info(chord: str) -> _ChordInfo:
    root, quality = parse_chord(chord)
    ivs = CHORD_TONES[quality]
    third = next(((root + i) % 12 for i in ivs if i in (3, 4)), None)
    fifth = next(((root + i) % 12 for i in ivs if i in (6, 7, 8)), None)
    return _ChordInfo(root, third, fifth, frozenset((root + i) % 12 for i in ivs))


def _cadence_pitch(info: _ChordInfo, cur: int, prev: int | None, complete: bool, lo: int, hi: int) -> int:
    """Final note of a section: complete cadence = root (the third if the root would leap more than a fifth), half
    cadence = the fifth (or the third / any non-root chord tone). Always the pitch closest to `cur`."""
    def nearest(pcs: list[int]) -> int | None:
        c = [p for p in range(lo, hi + 1) if p % 12 in pcs]
        return min(c, key=lambda p: (abs(p - cur), abs(p - prev) if prev is not None else 0, -p)) if c else None

    if complete:
        r, t = nearest([info.root]), nearest([info.third] if info.third is not None else [])
        if r is not None and (prev is None or abs(r - prev) <= 7 or t is None or abs(t - prev) > 7):
            return r
        return t if t is not None else cur
    pick = nearest([info.fifth] if info.fifth is not None else []) or nearest(
        [x for x in (info.third,) if x is not None]) or nearest(sorted(info.tones - {info.root}))
    return pick if pick is not None else cur


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
    spans: dict[int, tuple[int, int, int, int]] = {}   # phrase -> (start, end, bars, section)
    sec_ends: dict[int, int] = {}
    sec_pos, phrase_no = 0, 0
    for si, sec in enumerate(spec.sections):
        shift = 0 if si == 0 else rng_sec.choice(list(SECTION_SHIFTS))
        # energy-linked register: nudge the section toward lo + (hi - lo) * f(energy), at most CENTER_SHIFT_MAX steps
        target = lo + (hi - lo) * (CENTER_BASE + CENTER_ENERGY * sec.energy)
        mean_now = sum(pitch(i + shift) for i in idx_of) / len(idx_of)
        shift += max(-CENTER_SHIFT_MAX, min(CENTER_SHIFT_MAX, round((target - mean_now) / 1.7)))
        lengths = phrase_lengths(sec.bars)
        p_start = sec_pos
        for pi, plen in enumerate(lengths):
            p_end = p_start + plen * bar
            role = phrase_role(pi, len(lengths)) if len(lengths) > 1 else "presentation"
            cum, last_dir, run = 0, 0, 0
            phrase_t0 = p_start
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
            spans[phrase_no] = (phrase_t0, p_end, plen, si)
            phrase_no += 1
            p_start = p_end
        sec_ends[si] = p_start
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

    n = len(line)
    tones = [chord_tones(slots[slot_of(e.t)][2]) for e in line]
    strong = [e.t % bar in strong_pos or e.t == starts[slot_of(e.t)] for e in line]
    final = list(pitches)

    def _want(j: int) -> int:
        return (pitches[j] > pitches[j - 1]) - (pitches[j] < pitches[j - 1]) if j else 0
    scale_pcs = {(base + iv[i]) % 12 for i in range(7)}
    last_sec = len(spec.sections) - 1
    phrase_last = {j for j in range(n) if j + 1 == n or line[j + 1].phrase != line[j].phrase}
    sec_last = {j for j in phrase_last if spans[line[j].phrase][3] != (line[j + 1].sec if j + 1 < n else -1)}
    pinned: set[int] = set()   # notes whose pitch a later rule must not move (phrase ends / cadences)

    def is_ct(j: int) -> bool:
        return final[j] % 12 in tones[j]

    # 1) strong beats and chord changes are chord tones
    prev: int | None = None
    for j in range(n):
        if strong[j] and not is_ct(j):
            final[j] = _snap(final[j], tones[j], lo, hi, prev, _want(j))
        prev = final[j]

    # 2) phrase ends: chord tone, approached by step; section ends: cadence pitch (complete in the last section, half before)
    for j in sorted(phrase_last):
        if not is_ct(j):
            final[j] = _snap(final[j], tones[j], lo, hi, final[j - 1] if j else None)
        if j in sec_last:
            final[j] = _cadence_pitch(chord_tones_info(slots[slot_of(line[j].t)][2]), final[j], final[j - 1] if j else None,
                                      line[j].sec == last_sec, lo, hi)
        pinned.add(j)
        if j >= 2 and not strong[j - 1] and line[j - 1].phrase == line[j].phrase and abs(final[j] - final[j - 1]) > MAX_PASSTHROUGH:
            for c in sorted((final[j] + d for d in (-2, -1, 1, 2)), key=lambda c: (abs(c - final[j - 1]), -c)):
                if c % 12 in scale_pcs and lo <= c <= hi and (c % 12 in tones[j - 1] or (
                        final[j - 2] % 12 in tones[j - 2] and nonchord_role(final[j - 2], c, final[j]) is not None)):
                    final[j - 1] = c
                    break

    # 3) one apex per phrase, near APEX_POSITION of the phrase (other notes at that height step down to a chord tone)
    by_phrase: dict[int, list[int]] = {}
    for j in range(n):
        by_phrase.setdefault(line[j].phrase, []).append(j)
    for ph, js in by_phrase.items():
        top = max(final[j] for j in js)
        at_top = [j for j in js if final[j] == top]
        if len(at_top) < 2:
            continue
        p0, p1 = spans[ph][0], spans[ph][1]
        goal = p0 + APEX_POSITION * (p1 - p0)
        keep = {j for j in at_top if j in pinned} or {min(at_top, key=lambda j: (abs(line[j].t - goal), j))}
        for j in at_top:
            if j not in keep and j not in pinned and line[j].op != "orig":   # unmodified statements keep their own peak
                lower = frozenset(pc for pc in tones[j])
                cand = [p for p in range(lo, top) if p % 12 in lower]
                if cand:
                    ref = final[j - 1] if j else top - 1
                    final[j] = min(cand, key=lambda p: (abs(p - top) + LEAP_COST_WEIGHT * max(0, abs(p - ref) - LEAP_COST_FREE), -p))

    # 4) leaps (>= LEAP_MIN) are answered by a move the other way; 5) weak non-chord tones stay passing/neighbour; 6) no leap > octave
    def recover_leaps() -> bool:
        changed = False
        for j in range(1, n - 1):
            leap = final[j] - final[j - 1]
            if abs(leap) < LEAP_MIN or j + 1 in pinned or strong[j + 1]:
                continue
            back = final[j + 1] - final[j]
            if back * leap < 0:
                continue
            sign = -1 if leap > 0 else 1
            choice = None
            if j + 2 < n and is_ct(j) and is_ct(j + 2):
                for d in (1, 2):
                    c = final[j] + sign * d
                    if lo <= c <= hi and c % 12 in scale_pcs and nonchord_role(final[j], c, final[j + 2]) is not None:
                        choice = c
                        break
            if choice is None:
                cand = [p for p in range(lo, hi + 1) if p % 12 in tones[j + 1] and (p - final[j]) * sign > 0 and abs(p - final[j]) < abs(leap)]
                if cand:
                    choice = min(cand, key=lambda p: (abs(p - final[j]), -p))
            if choice is not None and choice != final[j + 1]:
                final[j + 1], changed = choice, True
        return changed

    def repair_weak() -> bool:
        changed = False
        for j in range(n):
            if is_ct(j):
                continue
            ok = (0 < j < n - 1 and is_ct(j - 1) and is_ct(j + 1) and nonchord_role(final[j - 1], final[j], final[j + 1]) is not None)
            if not ok:
                final[j] = _snap(final[j], tones[j], lo, hi, final[j - 1] if j else None, _want(j))
                changed = True
        return changed

    def clamp_octave() -> bool:
        changed = False
        for j in range(1, n):
            d = final[j] - final[j - 1]
            if abs(d) > LEAP_MAX:
                c = final[j] - 12 if d > 0 else final[j] + 12
                if lo <= c <= hi:
                    final[j], changed = c, True
        return changed

    recover_leaps()
    for _ in range(6):
        if not (repair_weak() | clamp_octave()):
            break

    # durations: v1's 0.92 gate, breath before phrase ends, complete cadence held to the end of the section
    breath, min_note = int(BREATH_BEATS * ppq), int(MIN_NOTE_BEATS * ppq)
    ends = [e.t + max(1, int(e.length * 0.92)) for e in line]
    for j in phrase_last:
        e = line[j]
        p1 = spans[e.phrase][1]
        if j in sec_last and e.sec == last_sec:
            ends[j] = sec_ends[e.sec]                 # complete cadence: sustain through the end of the piece
        elif p1 - breath - e.t >= min_note:
            ends[j] = min(ends[j], p1 - breath)
    for ph, (p0, p1, plen, si) in spans.items():
        if plen >= 4 and spec.sections[si].energy < BREATH_ENERGY_MAX:
            edge = p0 + 3 * bar
            inside = [j for j in by_phrase.get(ph, []) if line[j].t < edge]
            if inside:
                j = inside[-1]
                if ends[j] > edge - breath and edge - breath - line[j].t >= min_note:
                    ends[j] = edge - breath
    out: list[V2Note] = []
    for j, e in enumerate(line):
        sec = spec.sections[e.sec]
        out.append(V2Note(e.t, ends[j], final[j], velocity(sec.energy, rng_vel), e.phrase, e.pass_k, e.op, e.midx))
    return out
