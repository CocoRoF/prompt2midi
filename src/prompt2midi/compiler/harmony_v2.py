"""Compiler v2 harmony (docs/compiler-v2.md §5.3-5.4): the *playing* chord list and the bass line planned from it.

The spec's chords are never changed. `playing_slots` returns the same slot list as the compiler's `_slots` with the chord names
replaced by what v2 actually plays; melody (chord-tone correction), chords and bass all read this one list, so the three
never disagree. Everything is rule-based music theory (diatonic function groups, cadences, approach notes); the only
randomness is the substitution draw, from its own `Rng` label ("harmony.variation") and the bass fifths from the bass `Rng`.
No external data. Nothing here is evidence of musical quality - see docs/compiler-v2.md §7.

Order of the passes: substitution -> half cadence -> closing tonic -> V7 before I.
"""
from __future__ import annotations

from ..spec import MODE_INTERVALS, PITCH_CLASS, MusicSpec, parse_chord
from ..validate.theory import CHORD_TONES
from .rng import Rng

# --- Tuning constants (unverified by ear: initial values) ---
SUB_PROB = (0.0, 0.35, 0.6)   # chance to substitute an eligible chord, by how many earlier sections had the same progression (0, 1, 2+)
FIFTH_PROB = 0.6              # bass: chance of a fifth on an eligible beat (v1 used the same 0.6)
FIFTH_FROM_BEAT = 2           # bass: the fifth may enter from the 3rd beat of a slot on (0-based beat index)
FIFTH_MAX_PER_SLOT = 2
APPROACH_MIN_ENERGY = 0.5     # bass: approach notes and the beat line only from this section energy (v1: the same split)

SEVENTHS = frozenset({"7", "m7", "maj7", "m7b5"})
# function-group substitutions on diatonic triads, by scale degree: I -> vi (tonic group), IV -> ii (subdominant group)
SUBSTITUTE_DEGREE = {1: 6, 4: 2}
SUBDOMINANT_DEGREES = (4, 2)
_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
_QUALITY_BY_INTERVALS = {tuple(v): k for k, v in CHORD_TONES.items()}

Slot = tuple[int, int, str, int, object]


def tonic_pc(spec: MusicSpec) -> int:
    return PITCH_CLASS[spec.key.tonic.value]


def scale_pcs(spec: MusicSpec) -> frozenset[int]:
    return frozenset((tonic_pc(spec) + i) % 12 for i in MODE_INTERVALS[spec.key.mode.value])


def uses_sevenths(spec: MusicSpec) -> bool:
    return any(parse_chord(c)[1] in SEVENTHS for sec in spec.sections for c in sec.chords)


def diatonic_chord(spec: MusicSpec, degree: int, seventh: bool = False) -> str | None:
    """Chord on a scale degree (1..7) built from stacked scale thirds, named by the spec's chord qualities; None if the
    stack is not one of them."""
    iv = MODE_INTERVALS[spec.key.mode.value]
    steps = (0, 2, 4, 6) if seventh else (0, 2, 4)
    root = iv[degree - 1]
    rel = tuple((iv[(degree - 1 + s) % 7] + (12 if (degree - 1 + s) >= 7 else 0) - root) % 12 for s in steps)
    quality = _QUALITY_BY_INTERVALS.get(rel)
    if quality is None:
        return None
    return _NAMES[(tonic_pc(spec) + root) % 12] + quality


def degree_of(spec: MusicSpec, chord: str) -> int | None:
    """Scale degree whose diatonic *triad* is exactly this chord (same root pitch class and quality), else None."""
    root, quality = parse_chord(chord)
    for d in range(1, 8):
        name = diatonic_chord(spec, d)
        if name is not None and parse_chord(name) == (root, quality):
            return d
    return None


def recurrence(spec: MusicSpec) -> list[int]:
    """For every section: how many EARLIER sections have the same chord list and chords_per_bar."""
    seen: list[tuple[tuple[str, ...], int]] = []
    out = []
    for sec in spec.sections:
        key = (tuple(sec.chords), sec.chords_per_bar)
        out.append(sum(1 for k in seen if k == key))
        seen.append(key)
    return out


def substitution_probability(recur: int) -> float:
    return SUB_PROB[min(recur, len(SUB_PROB) - 1)]


def playing_slots(spec: MusicSpec, slots: list[Slot], seed: int) -> list[Slot]:
    """`slots` (the compiler's _slots as a list) with the chord names replaced by the chords v2 plays."""
    chords = [s[2] for s in slots]
    sevenths = uses_sevenths(spec)
    last_si = len(spec.sections) - 1
    by_section: dict[int, list[int]] = {}
    for i, s in enumerate(slots):
        by_section.setdefault(s[3], []).append(i)

    # 1) substitution within the function group (triad specs only; never the first slot of the piece nor the last section)
    if not sevenths:
        rng = Rng(seed, "harmony.variation")
        recur = recurrence(spec)
        for si, idxs in by_section.items():
            if si == last_si:
                continue
            for i in idxs:
                if i == 0:
                    continue
                d = degree_of(spec, chords[i])
                target = SUBSTITUTE_DEGREE.get(d) if d is not None else None
                name = diatonic_chord(spec, target) if target is not None else None
                if name is not None and rng.random() < substitution_probability(recur[si]):
                    chords[i] = name
        # 2) half cadence: a section (not the last) that ends on a subdominant chord ends on V
        v_triad = diatonic_chord(spec, 5)
        for si, idxs in by_section.items():
            if si != last_si and v_triad is not None and degree_of(spec, chords[idxs[-1]]) in SUBDOMINANT_DEGREES:
                chords[idxs[-1]] = v_triad

    # 3) closing: the piece ends on the tonic chord (unless the last chord already has the tonic as its root)
    tpc = tonic_pc(spec)
    if parse_chord(chords[-1])[0] != tpc:
        same_root = [c for c in (s[2] for s in slots) if parse_chord(c)[0] == tpc]   # keep the spec's own tonic colour (E7 in a blues)
        chords[-1] = same_root[-1] if same_root else (diatonic_chord(spec, 1, seventh=sevenths) or diatonic_chord(spec, 1) or chords[-1])

    # 4) V -> V7 where a section's last two chords are V then the tonic (triad specs; only when the seventh stays in the scale)
    if not sevenths:
        v7 = diatonic_chord(spec, 5, seventh=True)
        if v7 is not None and parse_chord(v7)[1] == "7":
            for idxs in by_section.values():
                if len(idxs) >= 2:
                    a, b = idxs[-2], idxs[-1]
                    if degree_of(spec, chords[a]) == 5 and parse_chord(chords[a])[1] == "" and parse_chord(chords[b])[0] == tpc:
                        chords[a] = v7
    return [(s[0], s[1], c, s[3], s[4]) for s, c in zip(slots, chords)]


def approach_note(next_root: int, root: int, lo: int, hi: int, scale: frozenset[int]) -> int | None:
    """A half or whole step below/above the next root (diatonic only), the one closest to the current root; ties go lower."""
    cands = [next_root + d for d in (-1, 1, -2, 2) if lo <= next_root + d <= hi and (next_root + d) % 12 in scale]
    return min(cands, key=lambda c: (abs(c - root), c)) if cands else None


def bass_slot_plan(start: int, length: int, root: int, fifth: int, next_root: int | None, energy: float, beat: int,
                   lo: int, hi: int, scale: frozenset[int], rng: Rng) -> list[tuple[int, int, int]]:
    """[(start, length, pitch)] for one chord slot. Low energy: the root for the whole slot. Otherwise a root per beat, the
    fifth from FIFTH_FROM_BEAT on (at most FIFTH_MAX_PER_SLOT times), and, when the next slot has another root, an approach note on
    the last beat (needs at least two beats in the slot)."""
    if energy < APPROACH_MIN_ENERGY:
        return [(start, length, root)]
    starts = list(range(start, start + length, beat))
    plan = [[s, min(beat, start + length - s), root] for s in starts]
    used = 0
    for k, item in enumerate(plan):
        if k >= FIFTH_FROM_BEAT and used < FIFTH_MAX_PER_SLOT and fifth != root and rng.random() < FIFTH_PROB:
            item[2] = fifth
            used += 1
    if next_root is not None and len(plan) >= 2 and next_root != root:
        a = approach_note(next_root, root, lo, hi, scale)
        if a is not None:
            plan[-1][2] = a
    return [tuple(x) for x in plan]
