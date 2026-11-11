"""PR1 of docs/compiler-v2.md: the opt-in `engine` field changes nothing about v1 output."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt2midi import spec as S
from prompt2midi.compiler import compile_spec
from prompt2midi.providers import schema as PS
from prompt2midi.providers import RuleProvider
from prompt2midi.validate import validate_schema, validate_spec

FIXTURES = Path(__file__).parent / "fixtures"
PROMPTS = ["잔잔한 로파이 피아노 느린 곡", "신나는 록 120 BPM", "슬픈 재즈 3/4박자", "밝은 팝 댄스곡 128bpm", "6/8박자 포크 민요"]


def _specs():
    r = RuleProvider()
    return [validate_spec(r.generate_spec(p))[0] for p in PROMPTS]


def _with(spec, engine):
    data = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    if engine is not None:
        data["engine"] = engine
    return S.MusicSpec.model_validate(data)


@pytest.mark.parametrize("seed", [None, 7])
def test_none_v1_v2_are_byte_identical(seed):
    for spec in _specs():
        base = spec.model_copy(update={"seed": seed})
        out = {e: compile_spec(_with(base, e)) for e in (None, "v1", "v2")}
        assert out["v1"] == out[None] == out["v2"]


def test_effective_seed_ignores_engine():
    for spec in _specs():
        seeds = {S.effective_seed(_with(spec, e)) for e in (None, "v1", "v2")}
        assert len(seeds) == 1
        assert S.canonical_json(_with(spec, "v2")) == S.canonical_json(_with(spec, None))
        assert '"engine"' not in S.canonical_json(_with(spec, "v2"))


def test_default_is_none_and_old_spec_files_load():
    spec = _specs()[0]
    assert spec.engine is None
    old = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "engine" not in old
    assert S.MusicSpec.model_validate(old).engine is None
    assert validate_spec(json.dumps(old))[0] is not None


def test_unknown_engine_value_rejected():
    data = _specs()[0].model_dump(mode="json", by_alias=True, exclude_none=True)
    data["engine"] = "v3"
    with pytest.raises(Exception):
        S.MusicSpec.model_validate(data)


def test_engine_is_not_in_llm_schemas():
    src = S.json_schema()
    assert "engine" in src["properties"]  # in the file schema (people/tools may set it) ...
    for make in (PS.schema_for_openai, PS.schema_for_anthropic):
        s = make(src)
        assert "engine" not in s["properties"]
        assert "engine" not in s.get("required", [])
    assert "engine" in src["properties"], "source schema must not be modified by the conversion"


def test_engine_spec_passes_schema_validation():
    data = _with(_specs()[0], "v2").model_dump(mode="json", by_alias=True, exclude_none=True)
    spec, issues = validate_schema(data)
    assert spec is not None and spec.engine == "v2" and not issues
