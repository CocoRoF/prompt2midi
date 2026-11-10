"""Pure music helpers shared by the validators and the compiler (no I/O, no randomness)."""
from __future__ import annotations

from .spec import (
    DEFAULT_REGISTER,
    INSTRUMENT_RANGE,
    MODE_INTERVALS,
    PITCH_CLASS,
    MusicSpec,
    Role,
    Track,
)

MIN_REGISTER_SPAN = 12  # every pitch class appears in any window of 12 semitones


def tonic_pc(spec: MusicSpec) -> int:
    return PITCH_CLASS[spec.key.tonic.value]


def effective_register(track: Track) -> tuple[int, int] | None:
    """Explicit register, or the role default clipped to the instrument range. None for drums.

    Returns the (possibly empty) clipped default as-is; callers check `hi - lo >= MIN_REGISTER_SPAN`.
    """
    if track.role is Role.drums:
        return None
    if track.pitch_range is not None:
        return track.pitch_range
    lo, hi = DEFAULT_REGISTER[track.role.value]
    ilo, ihi = INSTRUMENT_RANGE[track.instrument.value]
    return max(lo, ilo), min(hi, ihi)


def degree_offset(spec: MusicSpec, degree: int) -> int:
    """Semitones above the tonic for a scale degree (1 = tonic, 8 = octave up, -1 = 7th degree below)."""
    iv = MODE_INTERVALS[spec.key.mode.value]
    idx = degree - 1 if degree > 0 else degree
    return 12 * (idx // 7) + iv[idx % 7]


def shift_degree(degree: int, steps: int) -> int:
    """Move a degree by scale steps, skipping the non-existent degree 0."""
    n = degree + steps
    if degree > 0 and n <= 0:
        n -= 1
    elif degree < 0 and n >= 0:
        n += 1
    return n


def motif_pitches(spec: MusicSpec, track: Track, shift: int = 0) -> list[int] | None:
    """MIDI pitches of the motif degrees (+ `shift` scale steps) placed as low as the register allows.

    Returns None when the motif's span does not fit inside the register.
    """
    reg = effective_register(track)
    if track.motif is None or reg is None:
        return None
    lo, hi = reg
    degs = [shift_degree(d, shift) for d in track.motif.degrees]
    rel = [degree_offset(spec, d) for d in degs]
    tpc = tonic_pc(spec)
    base = lo - min(rel)  # lowest note at or above register low
    base += (tpc - base) % 12  # smallest tonic pitch >= base
    pitches = [base + r for r in rel]
    if min(pitches) < lo or max(pitches) > hi:
        return None
    return pitches
