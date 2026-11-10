"""MusicSpec: the closed, validated description of a piece of music.

The LLM (or a rule engine) only ever produces this structure; the compiler turns it into MIDI.
Every value space is closed: unknown fields, unknown enum values and out-of-range numbers are
rejected, never silently ignored (docs/design.md section 3).
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SPEC_VERSION = "1"
MAX_BARS_PER_SECTION = 32
MAX_SECTIONS = 16
MAX_TRACKS = 8
MAX_CHORDS_PER_SECTION = 16
MAX_MOTIF_NOTES = 32


class Tonic(str, Enum):
    C = "C"
    C_SHARP = "C#"
    D_FLAT = "Db"
    D = "D"
    D_SHARP = "D#"
    E_FLAT = "Eb"
    E = "E"
    F = "F"
    F_SHARP = "F#"
    G_FLAT = "Gb"
    G = "G"
    G_SHARP = "G#"
    A_FLAT = "Ab"
    A = "A"
    A_SHARP = "A#"
    B_FLAT = "Bb"
    B = "B"


PITCH_CLASS: dict[str, int] = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


class Mode(str, Enum):
    major = "major"
    minor = "minor"
    dorian = "dorian"
    mixolydian = "mixolydian"
    lydian = "lydian"
    phrygian = "phrygian"
    harmonic_minor = "harmonic_minor"


MODE_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}


class Role(str, Enum):
    melody = "melody"
    bass = "bass"
    chords = "chords"
    pad = "pad"
    drums = "drums"


class Instrument(str, Enum):
    """Instrument names -> General MIDI program numbers (0-based) in GM_PROGRAM."""

    piano = "piano"
    electric_piano = "electric_piano"
    organ = "organ"
    nylon_guitar = "nylon_guitar"
    steel_guitar = "steel_guitar"
    upright_bass = "upright_bass"
    electric_bass = "electric_bass"
    synth_bass = "synth_bass"
    violin = "violin"
    strings = "strings"
    trumpet = "trumpet"
    sax = "sax"
    flute = "flute"
    clarinet = "clarinet"
    marimba = "marimba"
    synth_lead = "synth_lead"
    synth_pad = "synth_pad"


GM_PROGRAM: dict[str, int] = {
    "piano": 0, "electric_piano": 4, "organ": 19, "nylon_guitar": 24, "steel_guitar": 25,
    "upright_bass": 32, "electric_bass": 33, "synth_bass": 38, "violin": 40, "strings": 48,
    "trumpet": 56, "sax": 65, "flute": 73, "clarinet": 71, "marimba": 12, "synth_lead": 80,
    "synth_pad": 88,
}

# Playable MIDI range (inclusive) used by the theory validator.
# Source check, 2026-11-10 (English Wikipedia, article text, https://en.wikipedia.org/wiki/<Instrument>):
#   confirmed: piano 21..108 (A0-C8, "Piano"); violin low G3=55 and high E7=100 ("Violin");
#              marimba 45..96 (most common 4.3-octave A2-C7, "Marimba").
#   NOT confirmed (my rounded approximations, no source opened): every other entry. In particular the
#   trumpet's article gives F#3-C6 without saying whether that is written or sounding pitch.
INSTRUMENT_RANGE: dict[str, tuple[int, int]] = {
    "piano": (21, 108), "electric_piano": (28, 103), "organ": (36, 96), "nylon_guitar": (40, 88),
    "steel_guitar": (40, 88), "upright_bass": (28, 67), "electric_bass": (28, 67),
    "synth_bass": (24, 72), "violin": (55, 100), "strings": (36, 96), "trumpet": (55, 82),
    "sax": (49, 81), "flute": (60, 96), "clarinet": (50, 94), "marimba": (45, 96),
    "synth_lead": (36, 96), "synth_pad": (36, 96),
}

DEFAULT_REGISTER: dict[str, tuple[int, int]] = {
    "melody": (60, 84), "bass": (36, 55), "chords": (48, 72), "pad": (48, 72),
}


class DrumStyle(str, Enum):
    rock_basic = "rock_basic"
    four_on_floor = "four_on_floor"
    half_time = "half_time"
    shuffle = "shuffle"
    bossa = "bossa"


class RhythmSymbol(str, Enum):
    """Note lengths: w=whole h=half q=quarter e=eighth s=sixteenth, '.'=dotted, r-prefix=rest."""

    w = "w"
    h = "h"
    q = "q"
    e = "e"
    s = "s"
    h_dot = "h."
    q_dot = "q."
    e_dot = "e."
    rw = "rw"
    rh = "rh"
    rq = "rq"
    re = "re"
    rs = "rs"


# length in quarter-note beats
RHYTHM_BEATS: dict[str, Fraction] = {
    "w": Fraction(4), "h": Fraction(2), "q": Fraction(1), "e": Fraction(1, 2), "s": Fraction(1, 4),
    "h.": Fraction(3), "q.": Fraction(3, 2), "e.": Fraction(3, 4),
    "rw": Fraction(4), "rh": Fraction(2), "rq": Fraction(1), "re": Fraction(1, 2), "rs": Fraction(1, 4),
}


def is_rest(symbol: str) -> bool:
    return symbol.startswith("r")


# chord names: root (flat/sharp allowed) + closed quality list
CHORD_QUALITIES = ("", "m", "7", "m7", "maj7", "dim", "aug", "sus2", "sus4", "m7b5")
CHORD_PATTERN = r"^[A-G](#|b)?(m7b5|maj7|dim|aug|sus2|sus4|m7|m|7)?$"
_CHORD_RE = re.compile(CHORD_PATTERN)

TIME_SIGNATURES = ((2, 4), (3, 4), (4, 4), (5, 4), (6, 8), (7, 8))

Seed = Annotated[int, Field(ge=0, le=2**32 - 1)]


def parse_chord(name: str) -> tuple[int, str]:
    """'Cm7' -> (pitch class 0, 'm7'). Raises ValueError for anything outside CHORD_PATTERN."""
    m = _CHORD_RE.match(name)
    if not m or name[:1] + (m.group(1) or "") not in PITCH_CLASS:
        raise ValueError(f"unknown chord: {name!r}")
    root = name[0] + (m.group(1) or "")
    return PITCH_CLASS[root], m.group(2) or ""


def _check_chord(name: str) -> str:
    parse_chord(name)  # ValueError -> schema issue at the offending list index
    return name


Chord = Annotated[str, StringConstraints(pattern=CHORD_PATTERN), AfterValidator(_check_chord)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class Key(_Strict):
    tonic: Tonic
    mode: Mode


class Motif(_Strict):
    """Scale degrees (1 = tonic, 8 = tonic an octave up, negatives go below) + rhythm symbols."""

    degrees: list[Annotated[int, Field(ge=-7, le=14)]] = Field(min_length=1, max_length=MAX_MOTIF_NOTES)
    rhythm: list[RhythmSymbol] = Field(min_length=1, max_length=MAX_MOTIF_NOTES * 2)

    @field_validator("degrees")
    @classmethod
    def _no_zero_degree(cls, v: list[int]) -> list[int]:
        if 0 in v:
            raise ValueError("degree 0 does not exist (degrees start at 1; negatives go below the tonic)")
        return v

    @model_validator(mode="after")
    def _rhythm_matches_degrees(self) -> "Motif":
        notes = sum(1 for r in self.rhythm if not is_rest(r.value))
        if notes != len(self.degrees):
            raise ValueError(f"rhythm has {notes} non-rest symbols but degrees has {len(self.degrees)} entries")
        return self


class Section(_Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9_]{1,8}$")
    bars: int = Field(ge=1, le=MAX_BARS_PER_SECTION)
    chords: list[Chord] = Field(min_length=1, max_length=MAX_CHORDS_PER_SECTION)
    chords_per_bar: Literal[1, 2] = 1
    energy: float = Field(ge=0.0, le=1.0, default=0.5)


class Track(_Strict):
    role: Role
    instrument: Instrument | None = None
    motif: Motif | None = None
    # JSON name is "register" (pydantic reserves that attribute name, hence the alias)
    pitch_range: tuple[Annotated[int, Field(ge=0, le=127)], Annotated[int, Field(ge=0, le=127)]] | None = Field(default=None, alias="register")
    style: DrumStyle | None = None

    @model_validator(mode="after")
    def _role_consistency(self) -> "Track":
        if self.role is Role.drums:
            if self.style is None:
                raise ValueError("drums track needs 'style'")
            if self.instrument is not None or self.motif is not None or self.pitch_range is not None:
                raise ValueError("drums track must not have instrument, motif or register")
        else:
            if self.instrument is None:
                raise ValueError(f"{self.role.value} track needs 'instrument'")
            if self.style is not None:
                raise ValueError("'style' is only for drums tracks")
            if self.role is Role.melody and self.motif is None:
                raise ValueError("melody track needs 'motif'")
            if self.role is not Role.melody and self.motif is not None:
                raise ValueError("'motif' is only for melody tracks")
        if self.pitch_range is not None and self.pitch_range[0] >= self.pitch_range[1]:
            raise ValueError("register must be [low, high] with low < high")
        return self


class MusicSpec(_Strict):
    version: Literal["1"] = SPEC_VERSION
    title: str = Field(min_length=1, max_length=60)
    seed: Seed | None = None
    tempo_bpm: int = Field(ge=40, le=220)
    time_signature: tuple[int, int] = (4, 4)
    key: Key
    sections: list[Section] = Field(min_length=1, max_length=MAX_SECTIONS)
    tracks: list[Track] = Field(min_length=1, max_length=MAX_TRACKS)

    @field_validator("time_signature")
    @classmethod
    def _allowed_signature(cls, v: tuple[int, int]) -> tuple[int, int]:
        if tuple(v) not in TIME_SIGNATURES:
            allowed = ", ".join(f"{a}/{b}" for a, b in TIME_SIGNATURES)
            raise ValueError(f"time_signature {v[0]}/{v[1]} not allowed (allowed: {allowed})")
        return v

    @model_validator(mode="after")
    def _unique_roles(self) -> "MusicSpec":
        roles = [t.role for t in self.tracks]
        dup = sorted({r.value for r in roles if roles.count(r) > 1})
        if dup:
            raise ValueError(f"each role may appear once, duplicated: {', '.join(dup)}")
        return self


def beats_per_bar(spec: MusicSpec) -> Fraction:
    """Bar length in quarter-note beats (6/8 -> 3, 7/8 -> 7/2)."""
    num, den = spec.time_signature
    return Fraction(4 * num, den)


def total_bars(spec: MusicSpec) -> int:
    return sum(s.bars for s in spec.sections)


def duration_seconds(spec: MusicSpec) -> float:
    return float(total_bars(spec) * beats_per_bar(spec) * 60 / spec.tempo_bpm)


def canonical_json(spec: MusicSpec) -> str:
    """Stable serialisation (sorted keys, no whitespace) used for hashing."""
    return json.dumps(spec.model_dump(mode="json", by_alias=True, exclude={"seed"}), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def effective_seed(spec: MusicSpec) -> int:
    """The spec's seed, or a 32-bit seed derived from the spec content (same spec -> same seed)."""
    if spec.seed is not None:
        return spec.seed
    return int.from_bytes(hashlib.sha256(canonical_json(spec).encode("utf-8")).digest()[:4], "big")


def json_schema() -> dict:
    """JSON Schema of MusicSpec (for structured-output APIs and documentation)."""
    return MusicSpec.model_json_schema()


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":  # python -m prompt2midi.spec > schemas/musicspec.v1.schema.json
    print(json_schema_text(), end="")
