import itertools
import json
from pathlib import Path

import pytest

from prompt2midi import presets
from prompt2midi import spec as S
from prompt2midi.compiler import compile_spec
from prompt2midi.expander import GENRES, assign_instruments, derive_seed, expand
from prompt2midi.intent import Genre, InstrumentName, Intent, Mood
from prompt2midi.validate import check_midi, validate_spec

DOC = Path(__file__).resolve().parent.parent / "docs" / "expander.md"
ALL_MOODS = [m.value for m in Mood]
ALL_GENRES = [g.value for g in Genre]
ALL_INSTRUMENTS = [i.value for i in InstrumentName]


def I(**kw):
    return Intent.model_validate(kw)


def roles_of(spec):
    return {t.role.value: (t.instrument.value if t.instrument else t.style.value) for t in spec.tracks}


def ok(ex):
    """The expansion validates, compiles and the MIDI passes MidiCheck with zero issues."""
    spec, report = validate_spec(ex.spec.model_dump(mode="json", by_alias=True))
    assert spec is not None and report.ok
    data = compile_spec(ex.spec)
    issues = check_midi(data, ex.spec)
    assert issues == [], [i.as_dict() for i in issues]
    return data


# ---------------- determinism ----------------

def test_same_intent_same_spec_and_bytes():
    a, b = expand(I(mood=["sad"], genre=["jazz"], tonic="D")), expand(I(genre=["jazz"], tonic="D", mood=["sad"]))
    assert a.spec == b.spec and a.explain_dicts() == b.explain_dicts()
    assert compile_spec(a.spec) == compile_spec(b.spec)


def test_seed_comes_from_the_musical_content():
    base = I(mood=["calm"], bars=9)
    assert expand(base).spec.seed == derive_seed(base) == expand(I(bars=9, mood=["calm"])).spec.seed
    assert derive_seed(I(mood=["calm"], bars=10)) != derive_seed(base)
    assert 0 <= derive_seed(base) < 2**32


def test_wording_metadata_does_not_change_the_music():
    plain = expand(I(mood=["calm"], tonic="A"))
    worded = expand(I(mood=["calm"], tonic="A", raw={"tonic": "Am", "mood": "calm-ish"}, conflicts=[{"fields": ["mood"], "reason": "x"}]))
    assert plain.spec == worded.spec and compile_spec(plain.spec) == compile_spec(worded.spec)
    assert any(d.field == "conflicts" for d in worded.explain), "conflicts are reported in explain, not used"


def test_any_musical_field_changes_the_result():
    base = expand(I(mood=["calm"]))
    for kw in ({"tonic": "D"}, {"mode": "minor"}, {"tempo_bpm": 100}, {"time_signature": "3/4"}, {"bars": 8}, {"mood": ["sad"]}, {"genre": ["rock"]}, {"instruments": ["flute"]}, {"chords": ["Am", "F"]}):
        other = expand(I(**{"mood": ["calm"], **kw}))
        assert other.spec != base.spec, kw


# ---------------- priority: explicit > genre > mood > default ----------------

def test_mode_priority():
    assert expand(I()).spec.key.mode.value == "major"  # default mood calm
    assert expand(I(mood=["sad"])).spec.key.mode.value == "minor"  # mood
    assert expand(I(mood=["sad"], genre=["blues"])).spec.key.mode.value == "major"  # genre beats mood
    assert expand(I(mood=["happy"], genre=["blues"], mode="minor")).spec.key.mode.value == "minor"  # explicit beats both
    assert expand(I(mood=["dark"])).spec.key.mode.value == "phrygian"


def test_tonic_priority_and_default_tonic_of_the_mode():
    assert expand(I(mood=["sad"])).spec.key.tonic.value == "A"  # minor -> A
    assert expand(I(mood=["sad"], tonic="D")).spec.key.tonic.value == "D" and expand(I(mood=["sad"], tonic="D")).spec.key.mode.value == "minor"
    assert (expand(I(mode="dorian")).spec.key.tonic.value, expand(I(mode="phrygian")).spec.key.tonic.value) == ("D", "E")
    ex = expand(I(tonic="Bb", mode="lydian", mood=["sad"], genre=["rock"]))
    assert (ex.spec.key.tonic.value, ex.spec.key.mode.value) == ("Bb", "lydian") and ex.decision("tonic").source == "intent"


def test_tempo_priority():
    assert expand(I(mood=["happy"], genre=["ballad"], tempo_bpm=100)).spec.tempo_bpm == 100
    assert expand(I(mood=["happy"], genre=["ballad"])).spec.tempo_bpm == 70
    assert expand(I(mood=["happy"])).spec.tempo_bpm == 120
    assert expand(I()).spec.tempo_bpm == 72
    ex = expand(I(mood=["happy"], genre=["ballad"]))
    assert (ex.decision("tempo_bpm").source, ex.decision("tempo_bpm").value) == ("genre:ballad", 70)


def test_energy_priority_and_several_moods():
    assert expand(I(mood=["calm"], genre=["rock"])).decision("energy").value == 0.8  # genre beats mood
    assert expand(I(mood=["happy"])).decision("energy").value == presets.MOODS["happy"][1]
    ex = expand(I(mood=["calm", "energetic"]))
    assert ex.decision("energy").value == round((0.25 + 0.9) / 2, 3)
    assert ex.spec.tempo_bpm == presets.MOODS["calm"][3] and ex.spec.key.mode.value == "major", "the first mood decides mode/tempo"
    assert "first (calm)" in ex.decision("mood").rule


def test_section_energy_follows_the_energy():
    ex = expand(I(mood=["calm"]))
    assert [s.energy for s in ex.spec.sections] == [0.25, 0.45, 0.25]
    assert [s.energy for s in expand(I(mood=["energetic"])).spec.sections] == [0.9, 1.0, 0.9]


def test_time_signature_and_bars():
    assert expand(I()).spec.time_signature == (4, 4) and expand(I(time_signature="6/8")).spec.time_signature == (6, 8)
    assert sum(s.bars for s in expand(I()).spec.sections) == 12
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 21, 50, 64):
        ex = expand(I(bars=n, tempo_bpm=200))
        assert sum(s.bars for s in ex.spec.sections) == n, n
        ok(ex)


def test_the_300_second_limit_is_the_only_override_of_an_explicit_value():
    ex = expand(I(bars=64, tempo_bpm=40))
    assert sum(s.bars for s in ex.spec.sections) == 50 and S.duration_seconds(ex.spec) <= 300
    d = ex.decision("bars")
    assert d.value == 50 and "clamped" in d.rule
    ok(ex)
    assert sum(s.bars for s in expand(I(bars=64, tempo_bpm=40, time_signature="6/8")).spec.sections) == 64  # 6/8 bars are shorter (288 s): no clamp


def test_explicit_chords_beat_every_preset_and_set_the_form():
    ex = expand(I(chords=["Am", "F", "C", "G"], genre=["jazz"], mood=["sad"]))
    assert all(s.chords == ["Am", "F", "C", "G"] for s in ex.spec.sections)
    assert ex.decision("chords").source == "intent"
    long = expand(I(chords=["C", "Am", "F", "G", "C", "Am", "F", "G"]))
    assert [s.bars for s in long.spec.sections] == [8, 8, 8], "three sections of len(chords) bars"
    assert [s.bars for s in expand(I(chords=["C", "G"])).spec.sections] == [4, 4, 4], "at least 12 bars"
    assert sum(s.bars for s in expand(I(chords=["C", "G"], bars=9)).spec.sections) == 9
    ok(ex), ok(long)


def test_default_progressions_follow_the_mode_table():
    for mode, (a, b) in presets.PROGRESSIONS.items():
        ex = expand(I(mode=mode, tonic="C"))
        pc = 0
        assert ex.spec.sections[0].chords == [presets.diatonic_chord(mode, pc, d) for d in a]
        assert ex.spec.sections[1].chords == [presets.diatonic_chord(mode, pc, d) for d in b]
        assert ex.spec.sections[2].chords == ex.spec.sections[0].chords


# ---------------- genre presets ----------------

DRUMS = {"pop": "rock_basic", "rock": "rock_basic", "jazz": "shuffle", "blues": "shuffle", "ballad": "half_time", "bossa_nova": "bossa",
         "electronic": "four_on_floor", "ambient": "half_time", "classical": None, "lofi": "half_time"}


@pytest.mark.parametrize("genre,style", list(DRUMS.items()))
def test_genre_picks_the_drum_style(genre, style):
    ex = expand(I(genre=[genre]))
    assert roles_of(ex.spec).get("drums") == style
    ok(ex)


def test_genre_table_matches_the_feasibility_table():
    assert {g: p.drums for g, p in GENRES.items()} == DRUMS and set(GENRES) == set(ALL_GENRES)
    assert GENRES["classical"].has_drums is False and all(GENRES[g].has_drums for g in GENRES if g != "classical")


def test_pop_progression_is_one_five_six_four_in_major():
    assert expand(I(genre=["pop"], tonic="C")).spec.sections[0].chords == ["C", "G", "Am", "F"]
    assert expand(I(genre=["pop"])).spec.key.mode.value == "major"


def test_jazz_bossa_lofi_use_sevenths_blues_uses_dominant_sevenths():
    for genre in ("jazz", "bossa_nova", "lofi"):
        chords = [c for s in expand(I(genre=[genre])).spec.sections for c in s.chords]
        assert all(any(c.endswith(q) for q in ("7", "m7", "maj7", "m7b5")) for c in chords), (genre, chords)
    assert expand(I(genre=["jazz"], tonic="C")).spec.sections[0].chords == ["Dm7", "G7", "Cmaj7", "Am7"]
    blues = expand(I(genre=["blues"], tonic="C"))
    assert blues.spec.sections[0].chords == ["C7", "F7", "C7", "G7"]
    assert {w.code for w in blues.warnings} == {"non_diatonic"}, "dominant 7ths outside the key are reported as warnings, by design"
    assert "as 7 chords" in blues.decision("chords").rule


def test_genre_instruments():
    assert roles_of(expand(I(genre=["rock"])).spec)["melody"] == "steel_guitar"
    r = roles_of(expand(I(genre=["ballad"])).spec)
    assert (r["melody"], r["pad"]) == ("piano", "strings")
    r = roles_of(expand(I(genre=["electronic"])).spec)
    assert (r["melody"], r["bass"], r["drums"]) == ("synth_lead", "synth_bass", "four_on_floor")
    assert roles_of(expand(I(genre=["ambient"])).spec)["pad"] == "synth_pad"
    r = roles_of(expand(I(genre=["classical"])).spec)
    assert "drums" not in r and (r["melody"], r["pad"]) == ("piano", "strings")
    assert roles_of(expand(I(genre=["lofi"])).spec)["melody"] == "electric_piano"
    assert expand(I(genre=["ballad"])).decision("energy").value == 0.3 and expand(I(genre=["ambient"])).decision("energy").value == 0.1


def test_the_first_genre_wins():
    ex = expand(I(genre=["jazz", "rock"]))
    assert roles_of(ex.spec)["drums"] == "shuffle" and "others are ignored: rock" in ex.decision("genre").rule


# ---------------- instruments ----------------

ASSIGN = [
    (["piano"], {"melody": "piano"}),
    (["piano", "strings"], {"melody": "piano", "chords": "strings"}),
    (["strings", "piano"], {"melody": "strings", "chords": "piano"}),
    (["synth_pad", "piano"], {"pad": "synth_pad", "melody": "piano"}),
    (["piano", "violin", "synth_pad", "electric_bass"], {"melody": "piano", "chords": "violin", "pad": "synth_pad", "bass": "electric_bass"}),
    (["organ", "piano", "strings"], {"melody": "organ", "chords": "piano", "pad": "strings"}),
    (["flute", "trumpet", "sax"], {"melody": "flute"}),
    (["electric_bass", "upright_bass", "synth_bass"], {"bass": "electric_bass"}),
    (["upright_bass", "piano"], {"bass": "upright_bass", "melody": "piano"}),
    (["drums"], {}), (["drums", "flute"], {"melody": "flute"}),
    (["piano", "electric_piano", "organ", "strings"], {"melody": "piano", "chords": "electric_piano", "pad": "organ"}),
    (["synth_pad", "synth_pad"], {"pad": "synth_pad"}),
    (["trumpet", "violin", "strings"], {"melody": "trumpet", "chords": "violin", "pad": "strings"}),
]


@pytest.mark.parametrize("listed,expected", ASSIGN, ids=lambda x: str(x)[:40])
def test_role_assignment_priority(listed, expected):
    roles, drums, _ign = assign_instruments(listed)
    assert roles == expected and drums is ("drums" in listed)


def test_ignored_instruments_are_explained():
    roles, _d, ignored = assign_instruments(["flute", "trumpet", "sax", "electric_bass", "synth_bass"])
    assert roles == {"melody": "flute", "bass": "electric_bass"}
    assert [i for i, _ in ignored] == ["trumpet", "sax", "synth_bass"]
    ex = expand(I(instruments=["flute", "trumpet"]))
    assert [(d.value, d.rule) for d in ex.explain if d.field == "instruments.ignored"] == [("trumpet", "no free role it is suited for")]


def test_explicit_instruments_are_the_whole_ensemble():
    assert roles_of(expand(I(instruments=["piano"], genre=["jazz"], mood=["energetic"])).spec) == {"melody": "piano"}, "no genre drums, no default bass/chords"
    r = roles_of(expand(I(instruments=["piano", "drums"], genre=["jazz"])).spec)
    assert r == {"melody": "piano", "drums": "shuffle"}, "listed drums take the genre's style"
    r = roles_of(expand(I(instruments=["drums", "piano"], mood=["sad"])).spec)
    assert r["drums"] == "half_time"  # energy 0.3 < 0.35
    r = roles_of(expand(I(instruments=["drums", "piano"], mood=["happy"])).spec)
    assert r["drums"] == "rock_basic"
    assert roles_of(expand(I(instruments=["drums"])).spec) == {"drums": "half_time"}
    assert expand(I(instruments=["drums"], genre=["classical"])).spec.tracks[0].style.value == "half_time", "explicit drums win over 'no drums'"


def test_global_instrument_defaults():
    assert roles_of(expand(I()).spec) == {"melody": "piano", "chords": "electric_piano"}
    assert roles_of(expand(I(mood=["energetic"])).spec) == {"melody": "piano", "chords": "electric_piano", "bass": "electric_bass", "drums": "rock_basic"}
    assert "bass" in roles_of(expand(I(mood=["dark"])).spec) and "drums" not in roles_of(expand(I(mood=["dark"])).spec)  # energy 0.5: bass yes, drums no
    assert roles_of(expand(I(mood=["meditative"])).spec).get("pad") == "synth_pad" and "chords" not in roles_of(expand(I(mood=["meditative"])).spec)
    assert roles_of(expand(I(mood=["epic"])).spec)["pad"] == "synth_pad"


# ---------------- validity of everything ----------------

@pytest.mark.parametrize("mood,genre", list(itertools.product(ALL_MOODS, ALL_GENRES)))
def test_every_mood_times_genre_gives_valid_midi(mood, genre):
    ok(expand(I(mood=[mood], genre=[genre])))


@pytest.mark.parametrize("mood", ALL_MOODS)
def test_every_mood_alone_and_every_genre_alone(mood):
    ok(expand(I(mood=[mood])))


@pytest.mark.parametrize("genre", ALL_GENRES)
def test_every_genre_alone(genre):
    ok(expand(I(genre=[genre])))


@pytest.mark.parametrize("instrument", ALL_INSTRUMENTS)
def test_every_single_instrument_ensemble(instrument):
    ok(expand(I(instruments=[instrument])))


def test_every_instrument_in_every_role_position_for_every_key():
    narrow = ["trumpet", "sax", "clarinet", "flute", "violin", "marimba", "organ", "steel_guitar"]
    for ins, tonic, mode in itertools.product(narrow, [t.value for t in S.Tonic], list(S.MODE_INTERVALS)):
        ex = expand(I(instruments=[ins, "piano", "drums"], tonic=tonic, mode=mode, mood=["epic"]))
        spec, report = validate_spec(ex.spec.model_dump(mode="json", by_alias=True))
        assert spec is not None and report.ok, (ins, tonic, mode)
    for ins in ("trumpet", "flute"):
        ok(expand(I(instruments=[ins], tonic="F#", mode="lydian", mood=["epic"])))


def test_the_fallback_motif_is_used_when_the_mood_motif_does_not_fit():
    hits = 0
    for tonic, mode in itertools.product([t.value for t in S.Tonic], list(S.MODE_INTERVALS)):
        ex = expand(I(instruments=["sax"], tonic=tonic, mode=mode, mood=["epic"]))
        if ex.decision("motif").value == "arch_slow":
            hits += 1
            assert "does not fit" in ex.decision("motif").rule
    assert hits > 0, "expected the wide 'rise' motif to be too wide for a sax register in some keys"


def test_nulls_everywhere_and_everything_filled():
    empty = expand(I())
    assert empty.spec.time_signature == (4, 4) and sum(s.bars for s in empty.spec.sections) == 12 and empty.spec.key.tonic.value == "C"
    ok(empty)
    full = expand(Intent.model_validate({
        "tonic": "F#", "mode": "dorian", "tempo_bpm": 133, "time_signature": "7/8", "bars": 21, "mood": ["calm", "dark", "epic"], "genre": ["jazz", "lofi", "ambient"],
        "instruments": ["piano", "violin", "synth_pad", "electric_bass", "drums"], "chords": ["Cm7", "F7", "Bbmaj7", "Edim", "Gaug", "Asus2", "Dsus4", "Em7b5", "G", "Am"],
        "conflicts": [{"fields": ["mood", "tempo_bpm"], "reason": "x"}], "raw": {"tonic": "Fis"}}))
    assert full.spec.tempo_bpm == 133 and full.spec.time_signature == (7, 8) and sum(s.bars for s in full.spec.sections) == 21
    assert roles_of(full.spec) == {"melody": "piano", "chords": "violin", "pad": "synth_pad", "bass": "electric_bass", "drums": "shuffle"}
    assert all(d.source == "intent" for d in full.explain if d.field in ("tonic", "mode", "tempo_bpm", "time_signature", "bars", "chords"))
    ok(full)


def test_multi_mood_multi_genre_combinations_are_valid():
    rng_moods = itertools.combinations(ALL_MOODS, 3)
    rng_genres = itertools.cycle(itertools.combinations(ALL_GENRES, 2))
    for moods, genres in itertools.islice(zip(rng_moods, rng_genres), 40):
        ok(expand(I(mood=list(moods), genre=list(genres))))


def test_every_time_signature_and_extreme_values():
    for sig in ("2/4", "3/4", "4/4", "5/4", "6/8", "7/8"):
        for bpm in (40, 200):
            for bars in (1, 64):
                ok(expand(I(time_signature=sig, tempo_bpm=bpm, bars=bars, genre=["jazz"])))


# ---------------- explain format ----------------

def test_explain_records_who_decided_what():
    ex = expand(I(mood=["happy"], genre=["jazz"], tempo_bpm=150))
    fields = {d.field for d in ex.explain}
    assert {"mood", "genre", "mode", "tonic", "tempo_bpm", "time_signature", "energy", "chords", "bars", "instruments", "drums", "motif"} <= fields
    for d in ex.explain_dicts():
        assert set(d) == {"field", "value", "source", "rule"} and d["rule"]
        assert d["source"] in ("intent", "default") or d["source"].startswith(("genre:", "mood:")), d
        json.dumps(d)  # JSON-serialisable
    assert ex.decision("tempo_bpm").source == "intent" and ex.decision("mode").source == "mood:happy" and ex.decision("tonic").source == "default"
    assert ex.decision("drums").source == "genre:jazz"


# ---------------- reuse, docs ----------------

def test_the_mood_table_is_shared_with_the_rule_provider_not_copied():
    from prompt2midi.providers import rule
    assert rule.MOODS is presets.MOODS and rule.MOTIFS is presets.MOTIFS and rule.PROGRESSIONS is presets.PROGRESSIONS and rule.DEFAULT_TONIC is presets.DEFAULT_TONIC
    for m in ALL_MOODS:
        ex = expand(I(mood=[m]))
        _t, energy, mode, bpm, motif = presets.MOODS[m]
        assert (ex.spec.tempo_bpm, ex.spec.key.mode.value) == (bpm, mode)
        assert ex.decision("energy").value == energy


def test_the_docs_list_every_genre_mood_and_the_listening_caveat():
    text = DOC.read_text(encoding="utf-8")
    for g, p in GENRES.items():
        assert f"`{g}`" in text and (p.drums is None or f"`{p.drums}`" in text), g
    for m, (_t, energy, mode, bpm, motif) in presets.MOODS.items():
        assert f"| `{m}` | {energy} | {mode} | {bpm} | `{motif}` |" in text, m
    assert "청취" in text and "확인하지 않았" in text
    for rule_word in ("melody", "chords", "pad", "bass", "drums"):
        assert rule_word in text


# ---------------- instruments_mode="fill" (used by the RuleProvider) ----------------

def F(**kw):
    return expand(I(**kw), instruments_mode="fill")


def test_the_default_mode_is_unchanged_ensemble_and_unknown_modes_are_rejected():
    assert expand(I(instruments=["piano"])).spec == expand(I(instruments=["piano"]), instruments_mode="ensemble").spec
    assert roles_of(expand(I(instruments=["piano"])).spec) == {"melody": "piano"}
    with pytest.raises(ValueError):
        expand(I(), instruments_mode="union")


def test_fill_keeps_named_instruments_and_fills_the_other_roles():
    assert roles_of(F(instruments=["piano"]).spec) == {"melody": "piano", "chords": "electric_piano"}, "ensemble mode would drop the chords"
    assert roles_of(F(instruments=["flute"], mood=["energetic"]).spec) == {"melody": "flute", "bass": "electric_bass", "drums": "rock_basic"}
    assert roles_of(F(instruments=["flute"], mood=["sad"]).spec) == {"melody": "flute", "chords": "piano"}
    assert roles_of(F(instruments=["flute", "drums"], mood=["sad"]).spec) == {"melody": "flute", "chords": "piano", "bass": "electric_bass", "drums": "half_time"}
    assert roles_of(F().spec) == {"melody": "piano", "chords": "electric_piano"}


def test_fill_follows_the_old_rule_set_for_roles():
    # chords only if no pad and energy < 0.7; bass if drums or energy >= 0.4; drums if named, from the genre, or (no genre) energy >= 0.7
    assert "chords" not in roles_of(F(mood=["energetic"]).spec) and "chords" in roles_of(F(mood=["sad"]).spec)
    assert "chords" not in roles_of(F(mood=["happy"]).spec) and "drums" in roles_of(F(mood=["happy"]).spec)
    assert "bass" in roles_of(F(mood=["dark"]).spec) and "bass" not in roles_of(F(mood=["calm"]).spec)
    assert roles_of(F(mood=["meditative"]).spec) == {"melody": "piano", "pad": "synth_pad"}
    assert roles_of(F(genre=["classical"], mood=["energetic"]).spec).get("drums") is None


def test_fill_role_assignment_matches_the_old_rule():
    from prompt2midi.expander.presets import assign_instruments_fill as A
    assert A(["organ", "sax"])[0] == {"melody": "sax", "chords": "organ"}, "an organ never leads"
    assert A(["organ"])[0] == {"chords": "organ"}
    assert A(["flute", "organ", "piano", "synth_lead"])[0] == {"melody": "flute", "chords": "organ"}
    assert A(["piano", "violin"])[0] == {"melody": "piano", "chords": "violin"}
    assert A(["piano", "strings"])[0] == {"melody": "piano", "pad": "strings"}, "strings are the pad, never chords"
    assert A(["strings", "trumpet"])[0] == {"melody": "strings", "pad": "strings"}, "the old rule's strings lead and pad"
    assert A(["synth_pad", "flute"])[0] == {"melody": "flute", "pad": "synth_pad"}
    assert A(["strings", "synth_pad"])[0]["pad"] == "strings"
    r, drums, ignored = A(["flute", "trumpet", "electric_bass", "synth_bass", "drums"])
    assert r == {"melody": "flute", "bass": "electric_bass"} and drums is True and [i for i, _ in ignored] == ["trumpet", "synth_bass"]


def test_fill_mode_expansions_are_valid_for_every_mood_genre_instrument():
    for mood, genre in itertools.product(ALL_MOODS, ALL_GENRES):
        ok(F(mood=[mood], genre=[genre]))
    for ins in ALL_INSTRUMENTS:
        ok(F(instruments=[ins]))
    for a, b in itertools.combinations(["piano", "violin", "flute", "organ", "strings", "synth_pad", "electric_bass", "drums", "sax"], 2):
        ok(F(instruments=[a, b], mood=["epic"]))


def test_fill_mode_is_deterministic():
    a = F(instruments=["flute", "organ"], mood=["happy"], genre=["jazz"])
    assert a.spec == F(instruments=["flute", "organ"], mood=["happy"], genre=["jazz"]).spec
