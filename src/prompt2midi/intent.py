"""Intent v1: what a sentence says, before any musical decision is made.

Layer 1 of the two-layer design (docs/프롬프트투미디/2단계-계획서-합의안.md):

    sentence --(rule provider or LLM)--> Intent v1 --(deterministic expander)--> MusicSpec --> MIDI

A provider only EXTRACTS: fields it cannot find stay null / empty (no guessing), contradictions go to `conflicts`,
words outside an enum become the nearest enum value plus the original wording in `raw`. Every musical decision
(chords, drums, instrument roles, defaults) belongs to `prompt2midi.expander`.

All value spaces are closed (unknown fields and enum values are rejected). The exported JSON Schema lists every field
as required (null / [] for "not stated") because OpenAI strict mode demands it; Python callers may omit them.
`raw` is a closed object (one optional string per field) rather than a free-form map: both providers reject open
objects in structured output (docs/providers.md).
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .spec import CHORD_PATTERN, Chord, Instrument, Mode, Tonic

INTENT_VERSION = "1"
TIME_SIGNATURE_VALUES = ("2/4", "3/4", "4/4", "5/4", "6/8", "7/8")
MAX_LIST = {"mood": 3, "genre": 3, "instruments": 8, "chords": 16, "conflicts": 8}
MAX_TEXT = 200


class Mood(str, Enum):
    calm = "calm"
    relaxing = "relaxing"
    sad = "sad"
    dark = "dark"
    happy = "happy"
    playful = "playful"
    energetic = "energetic"
    epic = "epic"
    romantic = "romantic"
    meditative = "meditative"


class Genre(str, Enum):
    pop = "pop"
    rock = "rock"
    jazz = "jazz"
    blues = "blues"
    ballad = "ballad"
    bossa_nova = "bossa_nova"
    electronic = "electronic"
    ambient = "ambient"
    classical = "classical"
    lofi = "lofi"


# the 17 instruments of MusicSpec plus the drum kit (a track role in MusicSpec, an "instrument" in a sentence)
InstrumentName = Enum("InstrumentName", {**{i.name: i.value for i in Instrument}, "drums": "drums"}, type=str)  # type: ignore[misc]


class FieldName(str, Enum):
    tonic = "tonic"
    mode = "mode"
    tempo_bpm = "tempo_bpm"
    time_signature = "time_signature"
    bars = "bars"
    mood = "mood"
    genre = "genre"
    instruments = "instruments"
    chords = "chords"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Text = Annotated[str, Field(max_length=MAX_TEXT)]


class Raw(_Strict):
    """The original wording behind a field, when it was normalised (e.g. 'Am' -> tonic A + mode minor)."""

    tonic: Text | None = None
    mode: Text | None = None
    tempo_bpm: Text | None = None
    time_signature: Text | None = None
    bars: Text | None = None
    mood: Text | None = None
    genre: Text | None = None
    instruments: Text | None = None
    chords: Text | None = None


class Conflict(_Strict):
    fields: Annotated[list[FieldName], Field(min_length=1, max_length=len(FieldName))]
    reason: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]


def _unique(values: list, what: str) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {what} values")
    return values


class Intent(_Strict):
    version: Literal["1"] = INTENT_VERSION
    tonic: Tonic | None = None
    mode: Mode | None = None
    tempo_bpm: Annotated[StrictInt, Field(ge=40, le=200)] | None = None
    time_signature: Literal["2/4", "3/4", "4/4", "5/4", "6/8", "7/8"] | None = None
    bars: Annotated[StrictInt, Field(ge=1, le=64)] | None = None
    mood: Annotated[list[Mood], Field(max_length=MAX_LIST["mood"])] = Field(default_factory=list)
    genre: Annotated[list[Genre], Field(max_length=MAX_LIST["genre"])] = Field(default_factory=list)
    instruments: Annotated[list[InstrumentName], Field(max_length=MAX_LIST["instruments"])] = Field(default_factory=list)
    chords: Annotated[list[Chord], Field(min_length=1, max_length=MAX_LIST["chords"])] | None = None
    conflicts: Annotated[list[Conflict], Field(max_length=MAX_LIST["conflicts"])] = Field(default_factory=list)
    raw: Raw = Field(default_factory=Raw)

    @field_validator("mood")
    @classmethod
    def _u_mood(cls, v):
        return _unique(v, "mood")

    @field_validator("genre")
    @classmethod
    def _u_genre(cls, v):
        return _unique(v, "genre")

    @field_validator("instruments")
    @classmethod
    def _u_instruments(cls, v):
        return _unique(v, "instruments")

    # ---- convenience ----
    def is_empty(self) -> bool:
        return (self.tonic is None and self.mode is None and self.tempo_bpm is None and self.time_signature is None and self.bars is None
                and not self.mood and not self.genre and not self.instruments and self.chords is None)

    def canonical_json(self) -> str:
        """Stable text of the whole Intent (sorted keys, no whitespace): the expander derives its seed from it."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_intent(data: Any) -> tuple[Intent | None, list[dict[str, str]]]:
    """dict / JSON text -> (Intent, []) or (None, [{path, message}]). Never raises."""
    from pydantic import ValidationError

    if isinstance(data, (str, bytes, bytearray)):
        try:
            data = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            return None, [{"path": "$", "message": f"not valid JSON: {exc}"}]
    if not isinstance(data, dict):
        return None, [{"path": "$", "message": f"intent must be a JSON object, got {type(data).__name__}"}]
    try:
        return Intent.model_validate(data), []
    except ValidationError as exc:
        out = []
        for e in exc.errors():
            path = ""
            for part in e["loc"]:
                path += f"[{part}]" if isinstance(part, int) else (("." if path else "") + str(part))
            msg = e["msg"][len("Value error, "):] if e["msg"].startswith("Value error, ") else e["msg"]
            out.append({"path": path or "$", "message": msg})
        return None, sorted(out, key=lambda d: (d["path"], d["message"]))


def json_schema() -> dict[str, Any]:
    """JSON Schema for provider structured output: every property required, no defaults (null / [] = not stated)."""

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: fix(v) for k, v in node.items() if k != "default"}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    return fix(Intent.model_json_schema())


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":  # python -m prompt2midi.intent > schemas/intent.v1.schema.json
    print(json_schema_text(), end="")
