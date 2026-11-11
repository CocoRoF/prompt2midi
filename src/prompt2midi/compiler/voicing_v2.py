"""Compiler v2 chord voicing (docs/compiler-v2.md §5.1-5.2, 5.5): minimal-movement voice leading.

Each chord is voiced with every chord tone exactly once, inside the track's register. The voicing of the next chord is the
candidate whose voices move least from the previous voicing (sum of absolute semitone moves), with voices matched in pitch
order - so voices never cross, a pitch both chords share is kept at the same height whenever the order allows it, and
inversions are a *result* of the search (nothing chooses an inversion). Chords of different size (seventh <-> triad)
match the smaller number of voices; a voice that has to enter is charged its distance to the nearest previous note.

Pure and deterministic: no randomness at all, ties go to the lower voicing. No external data. Parallel fifths/octaves are
NOT forbidden (decision on docs/compiler-v2.md §9.3). Nothing here is evidence of musical quality.
"""
from __future__ import annotations

from itertools import combinations, product

from ..spec import parse_chord
from ..validate.theory import CHORD_TONES

# Tuning constants (not music theory; the values are unverified by ear).
SPAN_MAX = 12       # semitones from the lowest to the highest voice before a spread voicing is penalised
SPAN_WEIGHT = 2.0    # cost per semitone beyond SPAN_MAX
COMPACT_WEIGHT = 0.25  # cost per semitone of span: closer voicings win when movement is similar

Voicing = tuple[int, ...]


def chord_pcs(chord: str) -> tuple[int, ...]:
    """Pitch classes of the chord tones, root first (each exactly once)."""
    root, quality = parse_chord(chord)
    return tuple((root + i) % 12 for i in CHORD_TONES[quality])


def candidates(pcs: tuple[int, ...], lo: int, hi: int) -> list[Voicing]:
    """Every voicing with each pitch class once, inside [lo, hi], as ascending tuples (sorted, so the search is ordered)."""
    options = [[p for p in range(lo, hi + 1) if p % 12 == pc] for pc in pcs]
    if any(not o for o in options):
        return []
    return sorted({tuple(sorted(c)) for c in product(*options)})


def match(prev: Voicing, cand: Voicing) -> tuple[float, list[tuple[int | None, int | None]]]:
    """Cheapest pitch-ordered (non-crossing) matching of voices: (cost, [(prev index | None, cand index | None), ...]).

    Equal sizes: voice i -> voice i. Different sizes: the smaller number of voices is matched in order, choosing which
    voices of the larger chord take part; an unmatched previous voice stops (free), an unmatched new voice is charged its
    distance to the nearest previous note."""
    m, k = len(prev), len(cand)
    best: tuple[float, list] | None = None
    if m >= k:
        for keep in combinations(range(m), k):
            cost = float(sum(abs(prev[a] - cand[b]) for b, a in enumerate(keep)))
            pairs = [(a, b) for b, a in enumerate(keep)] + [(a, None) for a in range(m) if a not in keep]
            if best is None or cost < best[0]:
                best = (cost, pairs)
    else:
        for keep in combinations(range(k), m):
            cost = float(sum(abs(prev[a] - cand[b]) for a, b in enumerate(keep)))
            for b in range(k):
                if b not in keep:
                    cost += min(abs(cand[b] - p) for p in prev)
            pairs = [(a, b) for a, b in enumerate(keep)] + [(None, b) for b in range(k) if b not in keep]
            if best is None or cost < best[0]:
                best = (cost, pairs)
    return best


def voicing_cost(prev: Voicing | None, cand: Voicing, center: float) -> float:
    """Movement from `prev` (or distance of the voicing's mean from the register centre for the first chord) + spread penalty."""
    span = cand[-1] - cand[0]
    spread = SPAN_WEIGHT * max(0, span - SPAN_MAX) + COMPACT_WEIGHT * span
    if prev is None:
        return abs(sum(cand) / len(cand) - center) + spread
    return match(prev, cand)[0] + spread


def next_voicing(chord: str, lo: int, hi: int, prev: Voicing | None) -> Voicing:
    """The cheapest voicing of `chord` after `prev` (None = first chord); ties go to the lowest voicing."""
    cands = candidates(chord_pcs(chord), lo, hi)
    if not cands:
        raise ValueError(f"register {lo}..{hi} cannot hold every tone of {chord}")
    center = (lo + hi) / 2
    return min(cands, key=lambda c: (voicing_cost(prev, c, center), c))
