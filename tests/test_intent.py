import json
from pathlib import Path

import pytest

from prompt2midi import intent as I
from prompt2midi import spec as S
from prompt2midi.providers import schema as PS
from prompt2midi.intent import Intent, validate_intent

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "intent.v1.schema.json"

FULL = {
    "version": "1", "tonic": "F#", "mode": "dorian", "tempo_bpm": 133, "time_signature": "7/8", "bars": 21,
    "mood": ["calm", "dark", "epic"], "genre": ["jazz", "lofi", "ambient"],
    "instruments": ["piano", "violin", "synth_pad", "electric_bass", "drums"],
    "chords": ["Cm7", "F7", "Bbmaj7", "Edim", "Gaug", "Asus2", "Dsus4", "Em7b5", "G", "Am"],
    "conflicts": [{"fields": ["mood", "tempo_bpm"], "reason": "calm but 180 bpm"}],
    "raw": {"tonic": "Fis", "mode": None, "tempo_bpm": "fast", "time_signature": None, "bars": None, "mood": "calm yet dark", "genre": None, "instruments": None, "chords": None},
}


def test_empty_and_full_intents_are_valid():
    i, errs = validate_intent({})
    assert errs == [] and i.is_empty() and i.mood == [] and i.chords is None and i.tonic is None
    i, errs = validate_intent(FULL)
    assert errs == [] and not i.is_empty() and i.tonic is S.Tonic.F_SHARP
    assert validate_intent(json.dumps(FULL))[1] == [] and validate_intent(json.dumps(FULL).encode())[1] == []


def test_a_provider_style_answer_with_every_field_present_and_nulls_is_valid():
    answer = {k: None for k in ("tonic", "mode", "tempo_bpm", "time_signature", "bars", "chords")}
    answer.update(version="1", mood=[], genre=[], instruments=[], conflicts=[], raw={k: None for k in I.Raw.model_fields})
    i, errs = validate_intent(answer)
    assert errs == [] and i.is_empty()


def mutated(**kw):
    d = json.loads(json.dumps(FULL))
    d.update(kw)
    return d


REJECTED = [
    ("unknown_field", mutated(extra=1), "extra"),
    ("wrong_version", mutated(version="2"), "version"),
    ("tonic_h", mutated(tonic="H"), "tonic"), ("tonic_lower", mutated(tonic="c"), "tonic"), ("tonic_E_sharp", mutated(tonic="E#"), "tonic"),
    ("mode_locrian", mutated(mode="locrian"), "mode"), ("mode_blues", mutated(mode="blues"), "mode"),
    ("tempo_39", mutated(tempo_bpm=39), "tempo_bpm"), ("tempo_201", mutated(tempo_bpm=201), "tempo_bpm"), ("tempo_str", mutated(tempo_bpm="120"), "tempo_bpm"),
    ("tempo_float", mutated(tempo_bpm=120.5), "tempo_bpm"), ("tempo_bool", mutated(tempo_bpm=True), "tempo_bpm"),
    ("sig_9_8", mutated(time_signature="9/8"), "time_signature"), ("sig_list", mutated(time_signature=[4, 4]), "time_signature"), ("sig_space", mutated(time_signature="4/4 "), "time_signature"),
    ("bars_0", mutated(bars=0), "bars"), ("bars_65", mutated(bars=65), "bars"), ("bars_str", mutated(bars="8"), "bars"),
    ("mood_dreamy", mutated(mood=["dreamy"]), "mood[0]"), ("mood_dup", mutated(mood=["calm", "calm"]), "mood"), ("mood_4", mutated(mood=["calm", "sad", "dark", "happy"]), "mood"),
    ("mood_str", mutated(mood="calm"), "mood"),
    ("genre_hiphop", mutated(genre=["hiphop"]), "genre[0]"), ("genre_dup", mutated(genre=["jazz", "jazz"]), "genre"), ("genre_4", mutated(genre=["pop", "rock", "jazz", "blues"]), "genre"),
    ("instr_harp", mutated(instruments=["harp"]), "instruments[0]"), ("instr_cello", mutated(instruments=["cello"]), "instruments[0]"),
    ("instr_dup", mutated(instruments=["piano", "piano"]), "instruments"), ("instr_9", mutated(instruments=["piano", "violin", "flute", "sax", "trumpet", "clarinet", "organ", "marimba", "strings"]), "instruments"),
    ("chords_empty", mutated(chords=[]), "chords"), ("chords_bad", mutated(chords=["Cx"]), "chords[0]"), ("chords_17", mutated(chords=["C"] * 17), "chords"),
    ("chords_ninth", mutated(chords=["C9"]), "chords[0]"), ("chords_slash", mutated(chords=["C/E"]), "chords[0]"),
    ("conflict_unknown_field", mutated(conflicts=[{"fields": ["volume"], "reason": "x"}]), "conflicts[0].fields[0]"),
    ("conflict_no_fields", mutated(conflicts=[{"fields": [], "reason": "x"}]), "conflicts[0].fields"),
    ("conflict_no_reason", mutated(conflicts=[{"fields": ["mood"], "reason": ""}]), "conflicts[0].reason"),
    ("conflict_extra", mutated(conflicts=[{"fields": ["mood"], "reason": "x", "severity": 1}]), "conflicts[0].severity"),
    ("conflicts_9", mutated(conflicts=[{"fields": ["mood"], "reason": "x"}] * 9), "conflicts"),
    ("raw_unknown_key", mutated(raw={"volume": "loud"}), "raw.volume"), ("raw_too_long", mutated(raw={"mood": "x" * 201}), "raw.mood"), ("raw_not_str", mutated(raw={"mood": 5}), "raw.mood"),
]


@pytest.mark.parametrize("name,data,path", REJECTED, ids=[r[0] for r in REJECTED])
def test_intent_rejects(name, data, path):
    i, errs = validate_intent(data)
    assert i is None and errs, f"{name} was accepted"
    assert any(path == e["path"] or e["path"].startswith(path) for e in errs), (name, errs)
    assert all(e["message"] for e in errs)


@pytest.mark.parametrize("bad", ["", "nope", "[1]", "null", "7", b"\xff", None, [], 7])
def test_non_object_input(bad):
    i, errs = validate_intent(bad)
    assert i is None and errs and errs[0]["path"] == "$"


def test_errors_are_sorted_and_stable():
    a = validate_intent({"tonic": "H", "mode": "x", "bars": 0})[1]
    assert a == validate_intent({"bars": 0, "mode": "x", "tonic": "H"})[1] and [e["path"] for e in a] == sorted(e["path"] for e in a)


def test_canonical_json_ignores_key_order():
    a = Intent.model_validate({"mood": ["calm"], "tonic": "C"})
    b = Intent.model_validate({"tonic": "C", "mood": ["calm"]})
    assert a.canonical_json() == b.canonical_json()


# ---------------- JSON Schema ----------------

def test_schema_file_is_in_sync():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == I.json_schema_text(), "stale: python -m prompt2midi.intent > schemas/intent.v1.schema.json"


def test_schema_enums_are_exactly_the_agreed_sets():
    d = I.json_schema()["$defs"]
    assert set(d["Mood"]["enum"]) == {"calm", "relaxing", "sad", "dark", "happy", "playful", "energetic", "epic", "romantic", "meditative"}
    assert "dreamy" not in d["Mood"]["enum"]
    assert set(d["Genre"]["enum"]) == {"pop", "rock", "jazz", "blues", "ballad", "bossa_nova", "electronic", "ambient", "classical", "lofi"}
    assert "hiphop" not in d["Genre"]["enum"]
    assert set(d["InstrumentName"]["enum"]) == {i.value for i in S.Instrument} | {"drums"} and len(d["InstrumentName"]["enum"]) == 18
    assert len(d["Tonic"]["enum"]) == 17 and set(d["Tonic"]["enum"]) == {t.value for t in S.Tonic}
    assert set(d["Mode"]["enum"]) == set(S.MODE_INTERVALS) and len(d["Mode"]["enum"]) == 7
    props = I.json_schema()["properties"]
    ts = next(a for a in props["time_signature"]["anyOf"] if "enum" in a)
    assert ts["enum"] == ["2/4", "3/4", "4/4", "5/4", "6/8", "7/8"]
    tempo = next(a for a in props["tempo_bpm"]["anyOf"] if a.get("type") == "integer")
    bars = next(a for a in props["bars"]["anyOf"] if a.get("type") == "integer")
    assert (tempo["minimum"], tempo["maximum"], bars["minimum"], bars["maximum"]) == (40, 200, 1, 64)


def test_mood_enum_equals_the_mood_preset_table():
    from prompt2midi.presets import MOODS
    assert {m.value for m in I.Mood} == set(MOODS)


def objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from objects(v)


def test_schema_is_closed_and_every_field_required():
    s = I.json_schema()
    assert s["type"] == "object" and s["additionalProperties"] is False
    for o in objects(s):
        assert o.get("additionalProperties") is False, "open objects are rejected by both providers"
        assert o["required"] == list(o["properties"])
    assert "default" not in json.dumps(s)


@pytest.mark.parametrize("make", [PS.schema_for_openai, PS.schema_for_anthropic], ids=["openai", "anthropic"])
def test_provider_conversions_of_the_intent_schema(make):
    out = make(I.json_schema())
    keys = set()

    def walk(n):
        if isinstance(n, dict):
            keys.update(n)
            [walk(v) for v in n.values()]
        elif isinstance(n, list):
            [walk(v) for v in n]
    walk(out)
    assert not keys & {"prefixItems", "allOf", "not", "if", "then", "else", "default", "patternProperties"}
    assert out["type"] == "object" and "anyOf" not in out
    for o in objects(out):
        assert o["additionalProperties"] is False


def test_openai_strict_rules_for_intent():
    out = PS.schema_for_openai(I.json_schema())
    for o in objects(out):
        assert o["required"] == list(o["properties"])
    assert PS.count_properties(out) <= PS.OPENAI_MAX_PROPERTIES and PS.object_depth(out) <= PS.OPENAI_MAX_DEPTH


def test_anthropic_rules_for_intent():
    out = PS.schema_for_anthropic(I.json_schema())
    banned = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "maxItems", "multipleOf"}
    found = set()

    def walk(n):
        if isinstance(n, dict):
            found.update(set(n) & banned)
            if "minItems" in n:
                assert n["minItems"] in (0, 1)
            [walk(v) for v in n.values()]
        elif isinstance(n, list):
            [walk(v) for v in n]
    walk(out)
    assert not found, found
    optional = unions = 0
    for o in objects(out):
        req = set(o.get("required", []))
        for name, p in o["properties"].items():
            optional += name not in req
            unions += "anyOf" in p or isinstance(p.get("type"), list)
    assert optional <= 24 and unions <= 16, (optional, unions)


def test_constraints_the_providers_drop_are_still_enforced_by_our_validation():
    # Anthropic/OpenAI may return e.g. tempo 500 or 9 instruments: the Intent validator is the final check
    assert validate_intent(mutated(tempo_bpm=500))[0] is None
    assert validate_intent(mutated(instruments=["piano"] * 9))[0] is None
