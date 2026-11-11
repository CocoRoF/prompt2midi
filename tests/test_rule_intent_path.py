"""RuleProvider now runs sentence -> translate -> Intent -> expander(fill) -> MusicSpec. What must stay the same, and what changed on purpose."""
import hashlib
import json
from pathlib import Path

import pytest

from prompt2midi.compiler import compile_spec
from prompt2midi.providers import RuleProvider
from prompt2midi.validate import validate_spec

FIX = Path(__file__).resolve().parent / "fixtures"
DATA = Path(__file__).resolve().parent.parent / "data"
RULE = RuleProvider()
RESEARCH = {p["id"]: p for p in json.loads((DATA / "eval_prompts_research1.json").read_text(encoding="utf-8"))}


def spec_of(prompt):
    spec, report = validate_spec(RULE.generate_spec(prompt))
    assert spec is not None and report.ok
    return spec


def sha(prompt):
    return hashlib.sha256(compile_spec(spec_of(prompt))).hexdigest()


def roles(prompt):
    return {t.role.value: (t.instrument.value if t.instrument else t.style.value) for t in spec_of(prompt).tracks}


# ---------------- unchanged by the migration ----------------

LEGACY = json.loads((FIX / "legacy_unchanged.json").read_text(encoding="utf-8"))["outputs"]


@pytest.mark.parametrize("prompt", list(LEGACY), ids=lambda p: p[:40])
def test_prompts_without_a_reason_to_change_give_the_same_midi_bytes_as_the_old_rule(prompt):
    assert sha(prompt) == LEGACY[prompt]


# old RuleProvider (commit e50d516) MIDI hashes of the research prompts that do NOT mention a genre or an unsupported pad word
UNCHANGED_RESEARCH = {1: "5837a935", 2: "a0f6731f", 3: "8533f3d8", 5: "69624914", 7: "bcc94b5c", 9: "3ed90aaa"}
# ... and the four that mention a genre word (rock, lo-fi jazz, ballad, ambient), which the expander's genre presets now apply
CHANGED_RESEARCH = {4: "b2a77e3b", 6: "ec9893e7", 8: "2eac67c0", 10: "c0bad042"}


@pytest.mark.parametrize("pid,old", list(UNCHANGED_RESEARCH.items()))
def test_research_prompts_without_genre_effects_are_byte_identical_to_the_old_rule(pid, old):
    assert sha(RESEARCH[pid]["text"])[:8] == old


@pytest.mark.parametrize("pid,old", list(CHANGED_RESEARCH.items()))
def test_research_prompts_with_a_genre_word_changed_only_by_the_genre_preset(pid, old):
    text = RESEARCH[pid]["text"]
    assert sha(text)[:8] != old
    spec = spec_of(text)
    assert spec.tempo_bpm == RESEARCH[pid]["bpm"] and f"{spec.key.tonic.value} {spec.key.mode.value}".replace("Bb", "Bb") == RESEARCH[pid]["key"]  # tempo and key as before


def test_the_four_changed_research_prompts_changed_in_exactly_these_ways():
    r4 = spec_of(RESEARCH[4]["text"])  # "energetic rock track": rock preset energy 0.8 instead of the mood's 0.9
    assert [s.energy for s in r4.sections] == [0.8, 1.0, 0.8] and roles(RESEARCH[4]["text"]) == {"melody": "steel_guitar", "bass": "electric_bass", "drums": "rock_basic"}
    r6 = spec_of(RESEARCH[6]["text"])  # "lo-fi jazz": seventh chords (jazz first), same instruments and drums as before
    assert r6.sections[0].chords == ["Gm7", "C7", "Fmaj7", "Dm7"] and roles(RESEARCH[6]["text"])["drums"] == "shuffle"
    assert roles(RESEARCH[6]["text"]) == {"melody": "electric_piano", "chords": "piano", "bass": "electric_bass", "drums": "shuffle"}
    assert roles(RESEARCH[8]["text"]) == {"melody": "piano", "chords": "violin", "pad": "strings", "bass": "electric_bass", "drums": "half_time"}  # ballad preset adds strings pad + drums
    assert [s.energy for s in spec_of(RESEARCH[8]["text"]).sections] == [0.3, 0.5, 0.3]
    assert roles(RESEARCH[10]["text"]) == {"melody": "nylon_guitar", "pad": "synth_pad", "bass": "electric_bass", "drums": "half_time"}  # ambient preset: drums per the feasibility table
    assert [s.energy for s in spec_of(RESEARCH[10]["text"]).sections] == [0.1, 0.3, 0.1]


@pytest.mark.parametrize("pid", range(1, 11))
def test_tempo_key_and_meter_of_all_ten_research_prompts_are_still_exact(pid):
    p = RESEARCH[pid]
    s = spec_of(p["text"])
    tonic, mode = p["key"].split(" ")
    assert (s.tempo_bpm, s.key.tonic.value, s.key.mode.value) == (p["bpm"], tonic, mode)
    assert s.time_signature == ((3, 4) if "3/4" in p["text"] else (4, 4))


# ---------------- the path itself ----------------

def test_the_output_keeps_the_old_shape():
    d = RULE.generate_spec("A calm piano piece in C major, 70 BPM")
    assert "seed" not in d, "the RuleProvider has never fixed a seed"
    assert d["title"] == "Calm C major"
    assert RULE.generate_spec("calm", seed=5)["seed"] == 5
    assert RULE.generate_spec("sad song in D dorian")["title"] == "Sad D dorian"
    assert RULE.generate_spec("hello")["title"] == "Calm C major"


def test_explain_shows_the_intent_and_the_expander_decisions_next_to_the_legacy_summary():
    why = RULE.explain("슬프고 어두운 현악 앙상블, A단조 재즈")
    assert why["key"] == "A minor" and why["mood"] == "dark"
    assert why["intent"]["tonic"] == "A" and why["intent"]["genre"] == ["jazz"] and why["intent"]["instruments"] == ["strings"]
    assert why["extraction"]["mood"] == "dark" and why["extraction"]["key"]["wording"] == "A단조"
    fields = {d["field"]: d for d in why["expander"]}
    assert fields["tonic"]["source"] == "intent" and fields["drums"]["source"] == "genre:jazz" and fields["mode"]["source"] == "intent"
    assert all(set(d) == {"field", "value", "source", "rule"} for d in why["expander"])
    json.dumps(why)  # JSON-serialisable


def test_legacy_summary_keys_and_default_strings_are_unchanged():
    why = RULE.explain("hello there")
    assert why["mood"] == "calm" and why["key"] == "C major" and "mood=calm (no mood word)" in why["defaults"] and "time_signature=4/4" in why["defaults"]
    assert any(d.startswith("key=C major") and "from mood" in d for d in why["defaults"]) and any(d.startswith("tempo=72") for d in why["defaults"])
    why = RULE.explain("A calm piano piece in C major with a 4/4 time signature at about 70 BPM")
    assert why["tempo_bpm"] == 70 and not any(d.startswith("tempo") for d in why["defaults"])
    assert why["lead"] == "piano" and why["bass"] is None and why["drums"] is None


# ---------------- changed on purpose (each reason is in the PR text and docs/rule-provider.md section 5) ----------------

def test_genre_words_are_now_applied():
    assert roles("a quiet jazz piece") == {"melody": "piano", "chords": "electric_piano", "bass": "electric_bass", "drums": "shuffle"}
    assert spec_of("a pop song").key.mode.value == "major" and spec_of("a sad pop song").key.mode.value == "major", "genre mode beats the mood's mode"
    assert "drums" not in roles("a classical piece"), "no genre word, so nothing changes"


def test_korean_tempo_terms_are_numbers_now():
    assert spec_of("신나는 아다지오").tempo_bpm == 66 and spec_of("슬픈 알레그로").tempo_bpm == 120 and spec_of("신나는 천천히").tempo_bpm == 66


def test_220_bpm_is_200_because_intent_v1_stops_at_200():
    s = spec_of("at 220 BPM")
    assert s.tempo_bpm == 200
    from prompt2midi.translate import translate
    assert translate("at 220 BPM").intent.conflicts[0].fields[0].value == "tempo_bpm"


def test_slow_and_cinematic_no_longer_choose_the_drum_style():
    """They were drum-style words of the old table; neither is a genre in the agreed enum. Drums now follow the energy (< 0.35 half_time)."""
    assert roles("energetic drums slow")["drums"] == "rock_basic"
    assert roles("calm drums slow")["drums"] == "half_time"


def test_orchestral_and_ensemble_alone_no_longer_add_a_strings_pad():
    """The old rule treated these words as 'pad wanted'. They name no instrument in the lexicon (and no words were added)."""
    assert roles("orchestral") == {"melody": "piano", "chords": "electric_piano"}
    assert roles("a string ensemble")["pad"] == "strings", "strings (named) still give the strings pad"
