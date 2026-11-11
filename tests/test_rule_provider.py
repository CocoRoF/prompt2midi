import json
import random
from pathlib import Path

import pytest

from prompt2midi.compiler import compile_spec
from prompt2midi.providers import RuleProvider
from prompt2midi.validate import check_midi, validate_spec

RULE = RuleProvider()
PROMPTS = json.loads((Path(__file__).resolve().parent.parent / "data" / "eval_prompts_research1.json").read_text(encoding="utf-8"))


def build(prompt):
    d = RULE.generate_spec(prompt)
    spec, report = validate_spec(d)
    assert spec is not None and report.ok, (prompt, [i.as_dict() for i in report.issues])
    data = compile_spec(spec)
    assert check_midi(data, spec) == [], prompt
    return d, spec, data


@pytest.mark.parametrize("p", PROMPTS, ids=lambda p: f"prompt{p['id']:02d}-{p['mood']}")
def test_research_prompts_tempo_key_and_signature_are_read_exactly(p):
    d, spec, _ = build(p["text"])
    tonic, mode = p["key"].split(" ")
    assert spec.tempo_bpm == p["bpm"]
    assert (spec.key.tonic.value, spec.key.mode.value) == (tonic, mode)
    want_sig = (3, 4) if "3/4" in p["text"] else (4, 4)
    assert spec.time_signature == want_sig


def test_research_prompts_instruments_and_drums():
    by_id = {p["id"]: build(p["text"])[1] for p in PROMPTS}
    roles = lambda s: {t.role.value: (t.instrument.value if t.instrument else t.style.value) for t in s.tracks}
    assert roles(by_id[3])["melody"] == "nylon_guitar" and roles(by_id[3])["drums"] == "rock_basic"
    assert roles(by_id[4])["melody"] == "steel_guitar" and "bass" in roles(by_id[4])
    assert roles(by_id[6])["melody"] == "electric_piano" and roles(by_id[6])["drums"] == "shuffle"  # lo-fi jazz
    assert roles(by_id[9])["melody"] == "synth_lead" and roles(by_id[9])["drums"] == "four_on_floor"
    assert roles(by_id[1]).keys() == {"melody", "chords"}  # calm piano piece: no drums
    assert "pad" in roles(by_id[10])


@pytest.mark.parametrize("p", PROMPTS, ids=lambda p: f"prompt{p['id']:02d}")
def test_same_prompt_same_bytes(p):
    assert build(p["text"])[2] == build(p["text"])[2]


def test_mood_changes_the_music():
    a, b = build("A sad song")[0], build("An energetic song")[0]
    assert a["key"]["mode"] != b["key"]["mode"] or a["tempo_bpm"] != b["tempo_bpm"]
    assert a["sections"][0]["energy"] < b["sections"][0]["energy"]
    assert not any(t["role"] == "drums" for t in a["tracks"]) and any(t["role"] == "drums" for t in b["tracks"])


@pytest.mark.parametrize("text,tonic,mode", [
    ("in B-flat major", "Bb", "major"), ("in Bb major", "Bb", "major"), ("in F# minor", "F#", "minor"),
    ("in F-sharp minor", "F#", "minor"), ("in C sharp dorian", "C#", "dorian"), ("in E harmonic minor", "E", "harmonic_minor"),
    ("a piece in D lydian", "D", "lydian"), ("in G mixolydian please", "G", "mixolydian"), ("in A phrygian", "A", "phrygian"),
])
def test_key_forms(text, tonic, mode):
    d, _, _ = build("A piece " + text)
    assert (d["key"]["tonic"], d["key"]["mode"]) == (tonic, mode)


@pytest.mark.parametrize("text,bpm", [("at 90 BPM", 90), ("at 40 bpm", 40), ("at 200 BPM", 200), ("at 220 BPM", 200), ("at 300 BPM", None), ("at 20 BPM", None)])
def test_bpm_bounds(text, bpm):
    d, _, _ = build("A piece " + text)
    if bpm is None:
        assert 40 <= d["tempo_bpm"] <= 200  # out-of-range request is ignored, mood default used
    else:
        assert d["tempo_bpm"] == bpm


@pytest.mark.parametrize("sig", ["2/4", "3/4", "4/4", "5/4", "6/8", "7/8"])
def test_allowed_signatures_are_read(sig):
    n, d = map(int, sig.split("/"))
    out, _, _ = build(f"A tune in {sig} time")
    assert out["time_signature"] == [n, d]


def test_unsupported_signature_falls_back_to_four_four():
    for sig in ("9/8", "12/8", "3/8"):
        assert build(f"A tune in {sig} time")[0]["time_signature"] == [4, 4]


def test_tempo_words_and_default():
    assert build("A very slow piece")[0]["tempo_bpm"] == 55
    assert build("A fast piece")[0]["tempo_bpm"] == 140
    d = build("")[0] if False else RULE.generate_spec("hello")
    assert 40 <= d["tempo_bpm"] <= 220


@pytest.mark.parametrize("text,role,inst", [
    ("an electric piano melody", "melody", "electric_piano"), ("acoustic guitar tune", "melody", "nylon_guitar"),
    ("electric guitar riff", "melody", "steel_guitar"), ("a flute solo", "melody", "flute"),
    ("trumpet fanfare", "melody", "trumpet"), ("violin piece", "melody", "violin"),
    ("with upright bass", "bass", "upright_bass"), ("with a synth bass and drums", "bass", "synth_bass"),
    ("with an organ and a saxophone", "melody", "sax"),
])
def test_instrument_words(text, role, inst):
    d, _, _ = build("A cheerful song " + text)
    got = {t["role"]: t.get("instrument") for t in d["tracks"]}
    assert got[role] == inst


@pytest.mark.parametrize("text,style", [("a bossa nova with drums", "bossa"), ("a dance track with drums", "four_on_floor"),
                                          ("a jazz trio with drums", "shuffle"), ("a ballad with drums", "half_time"),
                                          ("a punk song with drums", "rock_basic")])
def test_drum_style_words(text, style):
    d, _, _ = build(text)
    assert [t["style"] for t in d["tracks"] if t["role"] == "drums"] == [style]


def test_korean_prompt():
    d, spec, _ = build("잔잔한 피아노 곡, 느린 템포로 편안하게")
    assert spec.tracks[0].instrument.value == "piano" and spec.tempo_bpm < 100
    d2 = build("신나는 기타 음악 120 BPM 드럼")[0]
    assert d2["tempo_bpm"] == 120 and any(t["role"] == "drums" for t in d2["tracks"])


@pytest.mark.parametrize("text", ["", "   ", "asdf qwer zxcv", "!!!???", "🎵🎶", "x" * 2000, "A" * 50 + " minor", "major", "BPM", "99999999 BPM 1/1"])
def test_odd_prompts_still_give_a_valid_spec(text):
    build(text)


def test_explain_lists_defaults():
    why = RULE.explain("hello there")
    assert why["mood"] == "calm" and any("mood=calm" in x for x in why["defaults"]) and why["key"] == "C major"
    why = RULE.explain("A calm piano piece in C major with a 4/4 time signature at about 70 BPM")
    assert why["tempo_bpm"] == 70 and not any("tempo" in x for x in why["defaults"])


def test_seed_is_passed_through_and_prompt_is_not_modified():
    assert RULE.generate_spec("calm", seed=5)["seed"] == 5 and "seed" not in RULE.generate_spec("calm")
    prompt = "A calm piano piece"
    RULE.generate_spec(prompt)
    assert prompt == "A calm piano piece"


def test_each_call_returns_a_fresh_dict():
    a = RULE.generate_spec("calm piano")
    a["tracks"].clear()
    assert RULE.generate_spec("calm piano")["tracks"]


def test_fuzz_random_word_bags_always_validate_and_compile():
    words = ["calm", "sad", "happy", "dark", "epic", "romantic", "energetic", "piano", "guitar", "violin", "flute", "drums", "bass", "strings",
             "jazz", "rock", "dance", "ballad", "C major", "A minor", "Bb major", "F# dorian", "3/4", "6/8", "7/8", "5/4", "90 BPM", "150 BPM",
             "slow", "fast", "ambient", "pad", "electric piano", "synth lead", "trumpet", "organ", "marimba", "한글", "잔잔한", "신나는"]
    rng = random.Random(7)
    for _ in range(250):
        build(" ".join(rng.choice(words) for _ in range(rng.randint(1, 12))))
