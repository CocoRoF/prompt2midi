import pytest

from prompt2midi.validate import check_theory, validate_spec
from prompt2midi.validate.theory import chord_is_diatonic, scale_pitch_classes


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def spec_of(spec_dict):
    spec, report = validate_spec(spec_dict)
    assert spec is not None
    return spec


def test_valid_spec_has_no_issues(spec_dict):
    spec, report = validate_spec(spec_dict)
    assert spec is not None and report.issues == [] and report.ok


def test_scale_and_diatonic_chords(spec_dict):
    spec = spec_of(spec_dict)  # C minor
    assert scale_pitch_classes(spec) == {0, 2, 3, 5, 7, 8, 10}
    for c in ("Cm", "Ab", "Eb", "Bb", "Fm", "Gm", "Dm7b5", "Cm7", "Abmaj7"):
        assert chord_is_diatonic(spec, c), c
    for c in ("C", "G7", "A", "F#dim", "Cmaj7"):
        assert not chord_is_diatonic(spec, c), c


def test_non_diatonic_chord_is_a_warning_by_default(spec_dict):
    spec_dict["sections"][1]["chords"] = ["Fm", "G7"]  # G7 has B natural: outside C natural minor
    spec = spec_of(spec_dict)
    issues = check_theory(spec)
    assert codes(issues) == {"non_diatonic"} and codes(issues, "error") == set()
    assert issues[0].path == "sections[1].chords[1]" and issues[0].severity == "warning"


def test_non_diatonic_chord_is_an_error_when_strict(spec_dict):
    spec_dict["sections"][1]["chords"] = ["Fm", "G7"]
    spec = spec_of(spec_dict)
    issues = check_theory(spec, strict_diatonic=True)
    assert issues and all(i.severity == "error" for i in issues)
    _, report = validate_spec(spec_dict, strict_diatonic=True)
    assert not report.ok and report.feedback()[0]["path"] == "sections[1].chords[1]"


def test_harmonic_minor_makes_g7_diatonic(spec_dict):
    spec_dict["key"]["mode"] = "harmonic_minor"
    spec_dict["sections"][0]["chords"] = ["Cm", "G7", "Ab", "Fm"]
    spec_dict["sections"][1]["chords"] = ["Fm", "G7"]
    assert check_theory(spec_of(spec_dict)) == []


def test_duplicate_section_names(spec_dict):
    spec_dict["sections"][1]["name"] = "A"
    issues = check_theory(spec_of(spec_dict))
    assert "duplicate_section" in codes(issues, "error")
    assert [i.path for i in issues if i.code == "duplicate_section"] == ["sections[1].name"]


def test_too_long_by_duration(spec_dict):
    spec_dict["tempo_bpm"] = 40
    spec_dict["sections"] = [{"name": n, "bars": 32, "chords": ["Cm"]} for n in "ABCD"]  # 128 bars * 4 beats / 40bpm = 768 s
    issues = check_theory(spec_of(spec_dict))
    assert "too_long" in codes(issues, "error")
    assert "300" in [i.message for i in issues if i.code == "too_long"][0]


def test_exactly_at_the_duration_limit_is_allowed(spec_dict):
    # 300 s at 60 bpm in 4/4 = 75 bars
    spec_dict["tempo_bpm"] = 60
    spec_dict["sections"] = [{"name": "A", "bars": 32, "chords": ["Cm"]}, {"name": "B", "bars": 32, "chords": ["Cm"]}, {"name": "C", "bars": 11, "chords": ["Cm"]}]
    assert "too_long" not in codes(check_theory(spec_of(spec_dict)))
    spec_dict["sections"][2]["bars"] = 12  # 76 bars = 304 s
    assert "too_long" in codes(check_theory(spec_of(spec_dict)))


def test_too_many_bars_even_if_fast(spec_dict):
    spec_dict["tempo_bpm"] = 220
    spec_dict["sections"] = [{"name": f"S{i}", "bars": 32, "chords": ["Cm"]} for i in range(9)]  # 288 bars
    assert "too_long" in codes(check_theory(spec_of(spec_dict)), "error")


def test_register_outside_instrument_range(spec_dict):
    spec_dict["tracks"][0]["instrument"] = "trumpet"  # 55..82
    spec_dict["tracks"][0]["register"] = [60, 90]
    issues = check_theory(spec_of(spec_dict))
    assert "register_out_of_range" in codes(issues, "error")
    assert [i.path for i in issues if i.code == "register_out_of_range"] == ["tracks[0].register"]


def test_melody_register_too_narrow(spec_dict):
    spec_dict["tracks"][0]["register"] = [60, 66]
    assert "register_too_narrow" in codes(check_theory(spec_of(spec_dict)), "error")


def test_register_is_optional(spec_dict):
    del spec_dict["tracks"][0]["register"]
    assert check_theory(spec_of(spec_dict)) == []


def test_validate_spec_stops_at_schema_errors(spec_dict):
    spec_dict["tempo_bpm"] = 5
    spec, report = validate_spec(spec_dict)
    assert spec is None and not report.ok and report.feedback()[0]["path"] == "tempo_bpm"


def test_report_feedback_shape_for_llm_retry(spec_dict):
    spec_dict["sections"][1]["name"] = "A"
    _, report = validate_spec(spec_dict)
    fb = report.feedback()
    assert fb and set(fb[0]) == {"path", "message"}


@pytest.mark.parametrize("mode,chord,expected", [
    ("major", "C", True), ("major", "Am", True), ("major", "Bdim", True), ("major", "Bb", False),
    ("dorian", "D", False), ("dorian", "Dm7", True), ("dorian", "G", False), ("dorian", "Gm", True),
    ("lydian", "D", True), ("lydian", "F", False),
])
def test_diatonic_table(spec_dict, mode, chord, expected):
    spec_dict["key"] = {"tonic": "C", "mode": mode}
    assert chord_is_diatonic(spec_of(spec_dict), chord) is expected
