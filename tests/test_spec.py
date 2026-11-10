import json
from pathlib import Path

import pytest

from prompt2midi import spec as S
from prompt2midi.validate import validate_schema

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "musicspec.v1.schema.json"


def test_valid_spec_is_accepted(spec_dict):
    spec, issues = validate_schema(spec_dict)
    assert issues == []
    assert spec is not None and spec.title == "Night walk"
    assert spec.key.tonic is S.Tonic.C and spec.tracks[0].pitch_range == (60, 84)


def test_valid_spec_accepts_json_text_and_bytes(spec_dict):
    text = json.dumps(spec_dict)
    assert validate_schema(text)[1] == []
    assert validate_schema(text.encode("utf-8"))[1] == []


def test_defaults_are_filled(spec_dict):
    del spec_dict["time_signature"], spec_dict["seed"]
    spec, issues = validate_schema(spec_dict)
    assert issues == [] and spec.time_signature == (4, 4) and spec.seed is None


def _set(path, value):
    def apply(d):
        node = d
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
    return apply


def _delete(path):
    def apply(d):
        node = d
        for p in path[:-1]:
            node = node[p]
        del node[path[-1]]
    return apply


REJECTED = [
    ("unknown_top_field", lambda d: d.update(extra_field=1), "extra_field"),
    ("unknown_nested_field", _set(["key", "scale"], "x"), "key.scale"),
    ("wrong_version", _set(["version"], "2"), "version"),
    ("empty_title", _set(["title"], ""), "title"),
    ("long_title", _set(["title"], "x" * 61), "title"),
    ("missing_title", _delete(["title"]), "title"),
    ("negative_seed", _set(["seed"], -1), "seed"),
    ("huge_seed", _set(["seed"], 2**32), "seed"),
    ("tempo_low", _set(["tempo_bpm"], 39), "tempo_bpm"),
    ("tempo_high", _set(["tempo_bpm"], 221), "tempo_bpm"),
    ("tempo_string", _set(["tempo_bpm"], "fast"), "tempo_bpm"),
    ("tempo_float", _set(["tempo_bpm"], 96.5), "tempo_bpm"),
    ("signature_not_allowed", _set(["time_signature"], [5, 8]), "time_signature"),
    ("signature_wrong_length", _set(["time_signature"], [4]), "time_signature"),
    ("unknown_tonic", _set(["key", "tonic"], "H"), "key.tonic"),
    ("unknown_mode", _set(["key", "mode"], "locrian"), "key.mode"),
    ("no_sections", _set(["sections"], []), "sections"),
    ("too_many_sections", _set(["sections"], [{"name": f"S{i}", "bars": 1, "chords": ["C"]} for i in range(17)]), "sections"),
    ("zero_bars", _set(["sections", 0, "bars"], 0), "sections[0].bars"),
    ("too_many_bars", _set(["sections", 0, "bars"], 33), "sections[0].bars"),
    ("bad_section_name", _set(["sections", 0, "name"], "has space"), "sections[0].name"),
    ("empty_chords", _set(["sections", 0, "chords"], []), "sections[0].chords"),
    ("unknown_chord_quality", _set(["sections", 0, "chords", 1], "Cx"), "sections[0].chords[1]"),
    ("lowercase_chord", _set(["sections", 0, "chords", 0], "cm"), "sections[0].chords[0]"),
    ("impossible_chord_root", _set(["sections", 0, "chords", 0], "E#"), "sections[0].chords[0]"),
    ("energy_out_of_range", _set(["sections", 0, "energy"], 1.5), "sections[0].energy"),
    ("chords_per_bar_3", _set(["sections", 0, "chords_per_bar"], 3), "sections[0].chords_per_bar"),
    ("unknown_role", _set(["tracks", 1, "role"], "kazoo"), "tracks[1].role"),
    ("unknown_instrument", _set(["tracks", 0, "instrument"], "theremin"), "tracks[0].instrument"),
    ("unknown_drum_style", _set(["tracks", 2, "style"], "breakbeat"), "tracks[2].style"),
    ("melody_without_motif", _delete(["tracks", 0, "motif"]), "tracks[0]"),
    ("instrument_missing", _delete(["tracks", 1, "instrument"]), "tracks[1]"),
    ("drums_with_instrument", _set(["tracks", 2, "instrument"], "piano"), "tracks[2]"),
    ("drums_without_style", _delete(["tracks", 2, "style"]), "tracks[2]"),
    ("motif_on_bass", _set(["tracks", 1, "motif"], {"degrees": [1], "rhythm": ["q"]}), "tracks[1]"),
    ("style_on_melody", _set(["tracks", 0, "style"], "shuffle"), "tracks[0]"),
    ("degree_zero", _set(["tracks", 0, "motif", "degrees"], [0, 3, 5, 3]), "tracks[0].motif.degrees"),
    ("degree_too_high", _set(["tracks", 0, "motif", "degrees"], [1, 3, 5, 15]), "tracks[0].motif.degrees[3]"),
    ("rhythm_symbol_unknown", _set(["tracks", 0, "motif", "rhythm", 0], "x"), "tracks[0].motif.rhythm[0]"),
    ("rhythm_degree_mismatch", _set(["tracks", 0, "motif", "rhythm"], ["q", "e"]), "tracks[0].motif"),
    ("register_inverted", _set(["tracks", 0, "register"], [84, 60]), "tracks[0]"),
    ("register_out_of_midi", _set(["tracks", 0, "register"], [60, 128]), "tracks[0].register[1]"),
    ("duplicate_roles", lambda d: d["tracks"].append({"role": "bass", "instrument": "synth_bass"}), "$"),
    ("no_tracks", _set(["tracks"], []), "tracks"),
]


@pytest.mark.parametrize("name,mutate,where", REJECTED, ids=[r[0] for r in REJECTED])
def test_schema_rejects(spec_dict, name, mutate, where):
    mutate(spec_dict)
    spec, issues = validate_schema(spec_dict)
    assert spec is None, f"{name} was accepted"
    assert issues, "rejection must come with at least one issue"
    assert any(where in i.path for i in issues), f"{name}: no issue at {where!r}: {[i.as_dict() for i in issues]}"
    assert all(i.message for i in issues)


@pytest.mark.parametrize("bad", ["", "not json", "[1, 2]", "42", "null", b"\xff\xfe"])
def test_schema_rejects_non_object_input(bad):
    spec, issues = validate_schema(bad)
    assert spec is None and issues and issues[0].path == "$"


def test_schema_rejects_none_and_list():
    for bad in (None, [], 7):
        spec, issues = validate_schema(bad)
        assert spec is None and issues[0].code == "type"


def test_issue_messages_are_stable_and_sorted(spec_dict):
    spec_dict["tempo_bpm"] = 1
    spec_dict["title"] = ""
    _, a = validate_schema(spec_dict)
    _, b = validate_schema(spec_dict)
    assert a == b and [i.path for i in a] == sorted(i.path for i in a)


def test_unknown_enum_message_names_allowed_values(spec_dict):
    spec_dict["key"]["mode"] = "locrian"
    _, issues = validate_schema(spec_dict)
    assert issues[0].path == "key.mode"
    assert "major" in issues[0].message  # the message names the allowed values


# ---------- JSON Schema export ----------

def test_json_schema_file_is_in_sync():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == S.json_schema_text(), (
        "schemas/musicspec.v1.schema.json is stale: run `python -m prompt2midi.spec > schemas/musicspec.v1.schema.json`")


def test_json_schema_closes_every_object_and_enum():
    schema = S.json_schema()
    assert schema["additionalProperties"] is False
    for name, d in schema["$defs"].items():
        if d.get("type") == "object":
            assert d.get("additionalProperties") is False, f"{name} is open"
    assert set(schema["$defs"]["Role"]["enum"]) == {"melody", "bass", "chords", "pad", "drums"}
    assert "H" not in schema["$defs"]["Tonic"]["enum"]
    assert set(schema["$defs"]["Instrument"]["enum"]) == set(S.GM_PROGRAM)
    assert schema["properties"]["tempo_bpm"]["minimum"] == 40 and schema["properties"]["tempo_bpm"]["maximum"] == 220
    assert "register" in schema["$defs"]["Track"]["properties"]


def test_every_enum_member_has_a_table_entry():
    assert {i.value for i in S.Instrument} == set(S.GM_PROGRAM) == set(S.INSTRUMENT_RANGE)
    assert {r.value for r in S.RhythmSymbol} == set(S.RHYTHM_BEATS)
    assert {t.value for t in S.Tonic} == set(S.PITCH_CLASS)
    assert {m.value for m in S.Mode} == set(S.MODE_INTERVALS)
    for p in S.GM_PROGRAM.values():
        assert 0 <= p <= 127


# ---------- helpers ----------

def test_parse_chord():
    assert S.parse_chord("C") == (0, "")
    assert S.parse_chord("F#m7") == (6, "m7")
    assert S.parse_chord("Bbmaj7") == (10, "maj7")
    assert S.parse_chord("Em7b5") == (4, "m7b5")
    for bad in ("", "H", "Cmaj9", "c", "Cb", "E#"):
        with pytest.raises(ValueError):
            S.parse_chord(bad)


def test_duration_and_beats(spec_dict):
    spec, _ = validate_schema(spec_dict)
    assert S.beats_per_bar(spec) == 4 and S.total_bars(spec) == 8
    assert S.duration_seconds(spec) == pytest.approx(8 * 4 * 60 / 96)
    spec_dict["time_signature"] = [6, 8]
    spec68, _ = validate_schema(spec_dict)
    assert S.beats_per_bar(spec68) == 3


def test_effective_seed_is_deterministic_and_content_based(spec_dict):
    del spec_dict["seed"]
    a, _ = validate_schema(spec_dict)
    b, _ = validate_schema(spec_dict)
    assert S.effective_seed(a) == S.effective_seed(b)
    spec_dict["tempo_bpm"] = 97
    c, _ = validate_schema(spec_dict)
    assert S.effective_seed(a) != S.effective_seed(c)
    spec_dict["seed"] = 7
    d, _ = validate_schema(spec_dict)
    assert S.effective_seed(d) == 7
    assert 0 <= S.effective_seed(a) < 2**32
