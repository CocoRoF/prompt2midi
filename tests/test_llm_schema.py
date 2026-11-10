import copy
import json

import pytest

from prompt2midi.providers import schema as S
from prompt2midi.spec import json_schema
from prompt2midi.validate import validate_schema, validate_spec
from tests.conftest import VALID_SPEC


def objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from objects(v)


def all_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from all_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from all_keys(v)


def test_conversions_do_not_modify_the_source_schema():
    src = json_schema()
    before = copy.deepcopy(src)
    S.schema_for_openai(src), S.schema_for_anthropic(src)
    assert src == before and "prefixItems" in json.dumps(src)


@pytest.mark.parametrize("make", [S.schema_for_openai, S.schema_for_anthropic], ids=["openai", "anthropic"])
def test_common_rules(make):
    s = make(json_schema())
    keys = set(all_keys(s))
    assert not keys & {"prefixItems", "allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas", "default"}
    assert s["type"] == "object" and "anyOf" not in s  # root must be a plain object
    for o in objects(s):
        assert o.get("additionalProperties") is False
    assert make(json_schema()) == make(json_schema())  # deterministic


def test_openai_strict_rules():
    s = S.schema_for_openai(json_schema())
    for o in objects(s):
        assert o["required"] == list(o["properties"]), "every property must be required"
    assert S.count_properties(s) <= S.OPENAI_MAX_PROPERTIES and S.object_depth(s) <= S.OPENAI_MAX_DEPTH
    # optional fields are nullable unions, required ones are not
    track = s["$defs"]["Track"]["properties"]
    assert {"type": "null"} in track["instrument"]["anyOf"] and "anyOf" not in track["role"]
    assert {"type": "null"} in s["properties"]["seed"]["anyOf"]
    ts = s["properties"]["time_signature"]["anyOf"][0]
    assert ts["type"] == "array" and ts["minItems"] == ts["maxItems"] == 2 and ts["items"] == {"type": "integer"}
    # numeric constraints are supported by OpenAI and kept
    assert s["properties"]["tempo_bpm"]["minimum"] == 40 and s["properties"]["tempo_bpm"]["maximum"] == 220


def test_anthropic_rules():
    s = S.schema_for_anthropic(json_schema())
    banned = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "maxItems", "multipleOf"}
    assert not set(all_keys(s)) & banned
    def check_min_items(node):
        if isinstance(node, dict):
            if "minItems" in node:
                assert node["minItems"] in (0, 1)
            [check_min_items(v) for v in node.values()]
        elif isinstance(node, list):
            [check_min_items(v) for v in node]
    check_min_items(s)
    # the dropped constraints are kept as hints in the description
    assert "maximum: 220" in s["properties"]["tempo_bpm"]["description"] and "minimum: 40" in s["properties"]["tempo_bpm"]["description"]
    # complexity limits from the docs: <= 24 optional parameters, <= 16 union-typed parameters
    optional = unions = 0
    for o in objects(s):
        req = set(o.get("required", []))
        for name, p in o["properties"].items():
            optional += name not in req
            unions += "anyOf" in p or isinstance(p.get("type"), list)
    assert optional <= 24 and unions <= 16, (optional, unions)


def test_regex_in_chord_pattern_avoids_unsupported_features():
    pattern = json_schema()["$defs"]["Section"]["properties"]["chords"]["items"]["pattern"]
    for bad in ("(?=", "(?!", "(?<", "\\b", "\\B", "\\1"):
        assert bad not in pattern


def test_nullable_answer_round_trips_through_strip_nulls():
    answer = copy.deepcopy(VALID_SPEC)
    answer["version"] = None
    answer["time_signature"] = None
    for sec in answer["sections"]:
        sec["chords_per_bar"] = None
        sec["energy"] = None
    for t in answer["tracks"]:
        for k in ("instrument", "motif", "register", "style"):
            t.setdefault(k, None)
    answer["seed"] = None
    assert validate_schema(answer)[0] is None, "nulls on non-nullable fields must not validate by themselves"
    cleaned = S.strip_nulls(answer)
    spec, report = validate_spec(cleaned)
    assert spec is not None and report.ok
    assert spec.time_signature == (4, 4) and spec.sections[0].energy == 0.5 and spec.seed is None


def test_strip_nulls_is_recursive_and_keeps_falsy_values():
    assert S.strip_nulls({"a": None, "b": [{"c": None, "d": 0, "e": False, "f": ""}], "g": [1, None]}) == {"b": [{"d": 0, "e": False, "f": ""}], "g": [1, None]}
