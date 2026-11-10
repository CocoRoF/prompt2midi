"""Minimal drum patterns on an eighth-note grid, per time signature."""
from __future__ import annotations

KICK, SIDE_STICK, SNARE, CLOSED_HAT, OPEN_HAT, RIDE = 36, 37, 38, 42, 46, 51

# time signature -> (eighth steps per bar, kick steps, backbeat steps, pulse steps for four_on_floor)
GRID: dict[tuple[int, int], tuple[int, tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = {
    (2, 4): (4, (0,), (2,), (0, 2)),
    (3, 4): (6, (0,), (4,), (0, 2, 4)),
    (4, 4): (8, (0, 4), (2, 6), (0, 2, 4, 6)),
    (5, 4): (10, (0, 6), (4, 8), (0, 2, 4, 6, 8)),
    (6, 8): (6, (0,), (3,), (0, 3)),
    (7, 8): (7, (0, 4), (2,), (0, 2, 4)),
}

BOSSA_RIM_4_4 = (0, 3, 6)


def bar_hits(style: str, sig: tuple[int, int]) -> list[tuple[int, int, int]]:
    """Return [(step, gm_note, base_velocity 1..127)] for one bar; step counts eighth notes."""
    steps, kicks, backs, pulse = GRID[sig]
    hits: list[tuple[int, int, int]] = []
    if style == "rock_basic":
        hits += [(i, CLOSED_HAT, 70 if i % 2 == 0 else 55) for i in range(steps)]
        hits += [(k, KICK, 100) for k in kicks]
        hits += [(b, SNARE, 95) for b in backs]
    elif style == "four_on_floor":
        hits += [(p, KICK, 105) for p in pulse]
        hits += [(b, SNARE, 90) for b in backs]
        hits += [(p + 1, OPEN_HAT, 65) for p in pulse if p + 1 < steps]
    elif style == "half_time":
        hits += [(i, CLOSED_HAT, 65 if i % 2 == 0 else 50) for i in range(steps)]
        hits += [(0, KICK, 105)]
        hits += [(backs[len(backs) // 2] if len(backs) > 1 else backs[0], SNARE, 100)]
    elif style == "shuffle":
        hits += [(i, RIDE, 75 if i % 2 == 0 else 55) for i in range(steps)]
        hits += [(k, KICK, 95) for k in kicks]
        hits += [(b, SNARE, 80) for b in backs]
    elif style == "bossa":
        rim = BOSSA_RIM_4_4 if sig == (4, 4) else tuple(backs) + (0,)
        hits += [(i, CLOSED_HAT, 55) for i in range(steps)]
        hits += [(k, KICK, 85) for k in kicks]
        hits += [(r, SIDE_STICK, 70) for r in sorted(set(rim)) if r < steps]
    else:  # pragma: no cover - the enum is closed
        raise ValueError(style)
    return hits
