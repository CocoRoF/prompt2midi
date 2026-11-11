"""Evaluation set format (version "1"). The JSON Schema in schemas/evalset.v1.schema.json is generated from these models."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..intent import Genre
from ..spec import Instrument, Mode, Tonic

SET_VERSION = "1"
ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
SIGNATURE_PATTERN = r"^[1-9][0-9]?/(2|4|8|16)$"
EXPECT_INSTRUMENTS = [i.value for i in Instrument] + ["drums"]  # drums is a role, not an Instrument


class SetError(ValueError):
    """The set file is not a valid evaluation set. `.problems` lists every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("invalid evaluation set: " + "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
        self.problems = problems


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expect(_Strict):
    """Answer labels. Only properties the prompt states *in words*. Every field is optional; a metric whose
    label is missing is reported as 'na' (not applicable) and left out of its denominator."""

    tonic: Tonic | None = None
    mode: Mode | None = None
    tempo_bpm: Annotated[int, Field(ge=20, le=300)] | None = None
    time_signature: Annotated[str, Field(pattern=SIGNATURE_PATTERN)] | None = None
    bars: Annotated[int, Field(ge=1, le=512)] | None = None
    instruments: list[Literal[tuple(EXPECT_INSTRUMENTS)]] | None = Field(default=None, min_length=1)  # type: ignore[valid-type]
    # Optional (v0.4). Same list form and enum as Intent v1 `genre`. Today it only widens the scale-deviation limit
    # for jazz/blues (연구 1팀, 평가_기준_초안 2-3); genre match (3-4) is a pending item and does not use it.
    genre: Annotated[list[Genre], Field(min_length=1, max_length=3)] | None = None

    @field_validator("instruments", "genre")
    @classmethod
    def _unique(cls, v):
        if v is not None and len(set(v)) != len(v):
            raise ValueError("list items must not repeat")
        return v


class EvalItem(_Strict):
    id: Annotated[str, Field(pattern=ID_PATTERN)]
    lang: Literal["ko", "en"]
    tier: Literal["A", "B", "C"]
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    expect: Expect | None = None


class EvalSet(_Strict):
    version: Literal["1"]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    prompts: Annotated[list[EvalItem], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _unique_ids(self):
        seen, dup = set(), []
        for p in self.prompts:
            if p.id in seen:
                dup.append(p.id)
            seen.add(p.id)
        if dup:
            raise ValueError(f"duplicate prompt ids: {sorted(set(dup))}")
        return self


def json_schema() -> dict:
    return EvalSet.model_json_schema()


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def parse_set(data) -> EvalSet:
    try:
        return EvalSet.model_validate(data)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(x) for x in e['loc']) or '$'}: {e['msg']}" for e in exc.errors()]
        raise SetError(problems) from None


def load_set(path: str | Path) -> tuple[EvalSet, str]:
    """Read and validate a set file. Returns (set, sha256 of the file bytes). The path is only an argument:
    holdout sets are never kept in this repository (see docs/eval-metrics.md)."""
    import hashlib

    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetError([f"not valid UTF-8 JSON: {exc}"]) from None
    return parse_set(data), hashlib.sha256(raw).hexdigest()
