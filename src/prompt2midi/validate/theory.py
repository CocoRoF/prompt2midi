"""Step 2 of validation: musical and resource rules that the schema cannot express."""
from __future__ import annotations

from ..music import MIN_REGISTER_SPAN as _MIN_SPAN, effective_register, motif_pitches
from ..spec import (
    CHORD_QUALITIES,
    INSTRUMENT_RANGE,
    MODE_INTERVALS,
    PITCH_CLASS,
    Role,
    MusicSpec,
    beats_per_bar,
    duration_seconds,
    parse_chord,
    total_bars,
)
from .issues import Issue

MAX_DURATION_SECONDS = 300.0  # 5 minutes (design 4.2)
MAX_TOTAL_BARS = 256
MAX_ESTIMATED_NOTES = 20000

# chord quality -> intervals above the root
CHORD_TONES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "m7": (0, 3, 7, 10), "maj7": (0, 4, 7, 11),
    "dim": (0, 3, 6), "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "m7b5": (0, 3, 6, 10),
}
assert set(CHORD_TONES) == set(CHORD_QUALITIES)


def scale_pitch_classes(spec: MusicSpec) -> set[int]:
    tonic = PITCH_CLASS[spec.key.tonic.value]
    return {(tonic + i) % 12 for i in MODE_INTERVALS[spec.key.mode.value]}


def chord_is_diatonic(spec: MusicSpec, chord: str) -> bool:
    root, quality = parse_chord(chord)
    scale = scale_pitch_classes(spec)
    return all((root + i) % 12 in scale for i in CHORD_TONES[quality])


def check_theory(spec: MusicSpec, *, strict_diatonic: bool = False) -> list[Issue]:
    """Return issues for a schema-valid spec. Non-diatonic chords are warnings unless strict_diatonic."""
    issues: list[Issue] = []

    names = [s.name for s in spec.sections]
    for i, s in enumerate(spec.sections):
        if names.count(s.name) > 1 and names.index(s.name) != i:
            issues.append(Issue(f"sections[{i}].name", f"section name {s.name!r} is used more than once", "duplicate_section"))

    bars = total_bars(spec)
    if bars > MAX_TOTAL_BARS:
        issues.append(Issue("sections", f"{bars} bars in total exceeds the limit of {MAX_TOTAL_BARS}", "too_long"))
    dur = duration_seconds(spec)
    if dur > MAX_DURATION_SECONDS:
        issues.append(Issue("sections", f"piece lasts {dur:.0f}s, limit is {MAX_DURATION_SECONDS:.0f}s (shorten sections or raise tempo_bpm)", "too_long"))

    sev = "error" if strict_diatonic else "warning"
    for i, s in enumerate(spec.sections):
        for j, c in enumerate(s.chords):
            if not chord_is_diatonic(spec, c):
                issues.append(Issue(f"sections[{i}].chords[{j}]", f"chord {c} is not diatonic to {spec.key.tonic.value} {spec.key.mode.value}", "non_diatonic", sev))

    # rough upper bound on note events (every track can sound on every 16th)
    sixteenths = int(bars * beats_per_bar(spec) * 4)
    est = sixteenths * len(spec.tracks)
    if est > MAX_ESTIMATED_NOTES:
        issues.append(Issue("tracks", f"up to {est} note events exceeds the limit of {MAX_ESTIMATED_NOTES}", "too_many_notes"))

    for i, t in enumerate(spec.tracks):
        if t.role is Role.drums:
            continue
        lo, hi = INSTRUMENT_RANGE[t.instrument.value]
        if t.pitch_range is not None:
            a, b = t.pitch_range
            if a < lo or b > hi:
                issues.append(Issue(f"tracks[{i}].register", f"register {a}..{b} is outside the range of {t.instrument.value} ({lo}..{hi})", "register_out_of_range"))
        ra, rb = effective_register(t)
        if rb - ra < _MIN_SPAN:
            where = "register" if t.pitch_range is not None else "instrument"
            issues.append(Issue(f"tracks[{i}].{where}", f"{t.role.value} register {ra}..{rb} spans less than {_MIN_SPAN} semitones; set a wider 'register' that fits {t.instrument.value} ({lo}..{hi})", "register_too_narrow"))
        elif t.role is Role.melody and motif_pitches(spec, t) is None:
            issues.append(Issue(f"tracks[{i}].motif", f"the motif spans more than the register {ra}..{rb}; use fewer distant degrees or a wider register", "motif_out_of_register"))
    return issues
