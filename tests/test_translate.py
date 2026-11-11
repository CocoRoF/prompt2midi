"""translate: sentence -> Intent v1 (extraction only)."""
import json
import random
from pathlib import Path

import pytest

from prompt2midi import intent as I
from prompt2midi.translate import load_lexicon, translate

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"


def T(text):
    return translate(text).intent


def test_nothing_stated_means_nothing_extracted():
    for text in ("", "   ", "hello there", "Something nice for the evening", "🎵🎶", "x" * 2000):
        i = T(text)
        assert i.is_empty() and i.conflicts == [] and i.mood == [] and i.genre == [] and i.instruments == [], text


def test_extraction_never_decides_anything_musical():
    i = T("A cheerful rock song with electric guitar in A minor at 120 BPM in 3/4")
    assert i.bars is None and i.chords is None, "bars and chords are never extracted"
    import re
    for f in (ROOT / "src" / "prompt2midi" / "translate").glob("*.py"):
        imports = [ln for ln in f.read_text(encoding="utf-8").splitlines() if re.match(r"\s*(from|import)\s", ln)]
        assert not any("expander" in ln or "presets" in ln for ln in imports), f"{f.name} must not depend on musical presets: {imports}"


# ---------------- mood ----------------

def test_mood_is_the_one_with_the_most_hits_and_ties_follow_lexicon_order():
    assert T("a calm and peaceful tune").mood == [I.Mood.calm]
    assert T("calm calm sad").mood == [I.Mood.calm]
    assert T("sad and happy").mood == [I.Mood.sad], "tie: lexicon order (sad before happy)"
    assert T("행복한 노래").mood == [I.Mood.happy] and T("잔잔한 곡").mood == [I.Mood.calm]


def test_other_moods_and_the_matched_words_are_kept_in_raw():
    i = T("calm and relaxing, so relaxing")
    assert i.mood == [I.Mood.relaxing]
    assert "relax" in i.raw.mood and "also: calm" in i.raw.mood


# ---------------- genre ----------------

@pytest.mark.parametrize("text,genre", [
    ("a jazz piece", "jazz"), ("재즈 피아노", "jazz"), ("swing band", "jazz"), ("rock music", "rock"), ("록 밴드", "rock"), ("punk energy", "rock"),
    ("a pop song", "pop"), ("팝 음악", "pop"), ("a ballad", "ballad"), ("발라드", "ballad"), ("bossa nova", "bossa_nova"), ("보사노바", "bossa_nova"),
    ("samba rhythm", "bossa_nova"), ("blues", "blues"), ("electronic beats", "electronic"), ("techno", "electronic"), ("댄스 음악", "electronic"),
    ("ambient texture", "ambient"), ("lo-fi beats", "lofi"), ("lofi", "lofi"),
])
def test_genre_words(text, genre):
    assert genre in [g.value for g in T(text).genre]


def test_genre_list_follows_the_priority_order_not_the_text_order():
    assert [g.value for g in T("a rock song with jazz influences").genre] == ["jazz", "rock"]
    assert [g.value for g in T("pop and bossa nova").genre] == ["bossa_nova", "pop"]
    assert len(T("pop rock jazz blues ballad").genre) == 3, "at most three genres"


def test_hiphop_is_not_a_genre_its_wording_stays_in_raw():
    for text in ("a hiphop beat", "hip hop groove", "hip-hop", "힙합 비트"):
        i = T(text)
        assert i.genre == [] and "hiphop" in i.raw.genre and "genre enum" in i.raw.genre, text
    i = T("hiphop with jazz piano")
    assert [g.value for g in i.genre] == ["jazz"] and "hiphop" in i.raw.genre


def test_words_without_a_genre_in_the_enum_are_not_genres():
    for text in ("a slow piece", "cinematic mood", "a shuffle feel", "a classical piece", "클래식"):
        assert T(text).genre == [], text


# ---------------- tempo ----------------

@pytest.mark.parametrize("text,bpm", [("at 90 BPM", 90), ("120bpm", 120), ("55 BPM", 55), ("100 비피엠", 100), ("40 bpm", 40), ("200 BPM", 200)])
def test_numeric_tempo(text, bpm):
    i = T("A piece " + text)
    assert i.tempo_bpm == bpm and i.conflicts == []


@pytest.mark.parametrize("text,bpm,word", [
    ("느리게", 66, "느리게"), ("아주 느린", 55, "아주 느린"), ("very slow", 55, "very slow"), ("slow", 66, "slow"), ("adagio", 66, "adagio"), ("아다지오", 66, "아다지오"),
    ("천천히", 66, "천천히"), ("보통 빠르기", 96, "보통"), ("모데라토", 96, "모데라토"), ("안단테", 96, "안단테"), ("moderato", 96, "moderato"),
    ("알레그로", 120, "알레그로"), ("allegro", 120, "allegro"), ("빠르게", 140, "빠르게"), ("fast", 140, "fast"), ("presto", 170, "presto"), ("laid-back", 85, "laid-back"),
])
def test_tempo_words_become_numbers_and_the_word_stays_in_raw(text, bpm, word):
    i = T("a piece, " + text)
    assert i.tempo_bpm == bpm and word in i.raw.tempo_bpm


def test_the_longest_tempo_word_group_wins():
    assert T("a very slow and fast piece").tempo_bpm == 55  # 'very slow' (9 letters) beats 'fast'


def test_220_bpm_is_clamped_to_the_intent_range_with_a_conflict():
    i = T("at 220 BPM")
    assert i.tempo_bpm == 200 and "220" in i.raw.tempo_bpm
    assert [(c.fields[0].value, "clamped" in c.reason) for c in i.conflicts] == [("tempo_bpm", True)]
    assert T("at 201 BPM").tempo_bpm == 200


@pytest.mark.parametrize("text", ["at 300 BPM", "at 20 BPM", "at 39 BPM", "at 221 BPM"])
def test_out_of_range_bpm_is_ignored_like_the_old_rule(text):
    i = T("A piece " + text)
    assert i.tempo_bpm is None and text.split("at ")[1] in i.raw.tempo_bpm


def test_a_stated_bpm_beats_a_tempo_word_and_a_big_disagreement_is_a_conflict():
    i = T("a fast piece at 70 BPM")
    assert i.tempo_bpm == 70 and any("fast" in c.reason for c in i.conflicts)
    assert T("a slow piece at 62 BPM").conflicts == [], "62 vs 66: close enough"


# ---------------- key ----------------

def test_explicit_key_sets_tonic_and_mode_and_keeps_the_wording():
    i = T("슬프고 어두운 현악 앙상블, A단조")
    assert (i.tonic.value, i.mode.value, i.raw.tonic) == ("A", "minor", "A단조")
    i = T("in B-flat major")
    assert (i.tonic.value, i.mode.value) == ("Bb", "major")


def test_a_mode_word_alone_sets_only_the_mode():
    i = T("a dorian feel")
    assert i.tonic is None and i.mode.value == "dorian" and i.raw.mode == "dorian"
    assert T("행복한 단조 곡").mode.value == "minor"


def test_the_first_key_wins_and_several_keys_are_a_conflict():
    i = T("C장조로 시작해서 A단조로 끝나는")
    assert (i.tonic.value, i.mode.value) == ("C", "major")
    assert [f.value for f in i.conflicts[0].fields] == ["tonic", "mode"] and "C major" in i.conflicts[0].reason and "A minor" in i.conflicts[0].reason
    assert T("C장조 C major").conflicts == [], "the same key twice is no conflict"


def test_false_positives_stay_out():
    for text in ("사장조직 회의 음악", "마장조선시대 음악", "나 장조림 요리 노래", "바 단조건"):
        assert T(text).tonic is None, text


# ---------------- time signature ----------------

@pytest.mark.parametrize("text,sig", [("3/4", "3/4"), ("4분의 3박자", "3/4"), ("8분의 6박자", "6/8"), ("3박자", "3/4"), ("사분의 삼박자", "3/4"), ("7/8 time", "7/8"), ("2/4", "2/4")])
def test_time_signature(text, sig):
    assert T("piece in " + text).time_signature == sig


@pytest.mark.parametrize("text", ["9/8", "12/8", "3/8", "9분의 8박자", "2분의 3박자"])
def test_unsupported_meters_are_kept_in_raw_not_guessed(text):
    i = T("piece in " + text)
    assert i.time_signature is None and text.replace(" ", "") in i.raw.time_signature.replace(" ", "")


# ---------------- instruments ----------------

def names(text):
    return [x.value for x in T(text).instruments]


def test_instruments_in_text_order_with_bass_and_drums_last():
    assert names("a piece for piano and violin") == ["piano", "violin"]
    assert names("violin first, then piano") == ["violin", "piano"]
    assert names("flute, electric bass and drums") == ["flute", "electric_bass", "drums"]
    assert names("upright bass and piano") == ["piano", "upright_bass"]
    assert names("synth bass and drums") == ["synth_bass", "drums"]


def test_electric_and_acoustic_variants():
    assert names("an electric piano melody") == ["electric_piano"]
    assert names("electric guitar riff") == ["steel_guitar"] and names("acoustic guitar") == ["nylon_guitar"] and names("기타 연주") == ["nylon_guitar"]
    assert names("harp and flute")[0] == "nylon_guitar"


def test_pad_and_drum_words():
    assert names("soft pads") == ["synth_pad"] and names("패드 소리") == ["synth_pad"]
    for w in ("drum", "drums", "percussion", "timpani", "beat", "드럼", "타악"):
        assert names("with " + w) == ["drums"], w


def test_at_most_eight_instruments():
    i = T("piano violin flute sax trumpet clarinet marimba organ strings synth lead electric guitar bass drums")
    assert len(i.instruments) == 8 and "dropped" in i.raw.instruments


# ---------------- general ----------------

def test_deterministic_and_always_a_valid_intent():
    rng = random.Random(3)
    words = ["calm", "sad", "jazz", "rock", "hiphop", "piano", "drums", "A단조", "C major", "3/4", "4분의 3박자", "90 BPM", "250 BPM", "느리게", "아다지오", "🎵", "bass", "pads", "orchestral", "9/8"]
    for _ in range(400):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(0, 14)))
        a, b = translate(text).intent, translate(text).intent
        assert a == b
        assert I.validate_intent(a.model_dump(mode="json"))[1] == [], text


def test_the_trace_explains_the_matches():
    t = translate("sad rock in A minor at 70 BPM with piano").trace
    assert t["mood"] == "sad" and "rock" in t["genre_words"] and t["key"]["wording"] == "A minor" and t["tempo"]["source"] == "bpm" and t["instruments"] == ["piano"]


# ---------------- lexicon files ----------------

def test_lexicon_files_are_valid_and_use_only_agreed_ids():
    lex = load_lexicon()
    assert set(lex) == {"moods", "instruments", "genres", "tempo"}
    assert set(lex["moods"]["moods"]) == {m.value for m in I.Mood}
    assert {g["id"] for g in lex["genres"]["genres"]} == {g.value for g in I.Genre}
    ids = {i.value for i in I.InstrumentName}
    assert {s["id"] for s in lex["instruments"]["instruments"]} <= ids and {b["id"] for b in lex["instruments"]["bass"]} <= ids
    assert set(lex["instruments"]["lead_capable"]) <= ids
    assert all(40 <= g["bpm"] <= 200 for g in lex["tempo"]["words"])
    assert "hiphop" not in {g["id"] for g in lex["genres"]["genres"]}


def test_lexicon_files_are_plain_json_a_reviewer_can_read():
    for f in (ROOT / "src" / "prompt2midi" / "translate" / "lexicon").glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert "_note" in data, f.name


def test_no_old_word_was_lost_and_only_the_agreed_words_were_added():
    old = json.loads((FIX / "legacy_rule_words.json").read_text(encoding="utf-8"))
    lex = load_lexicon()
    assert {m: set(w) for m, w in lex["moods"]["moods"].items()} == {m: set(w) for m, w in old["moods"].items()}
    assert {s["id"]: set(s["words"]) for s in lex["instruments"]["instruments"]} == {k: set(v) for k, v in old["instruments"].items()}
    assert {b["id"]: set(b["words"]) for b in lex["instruments"]["bass"]} == {k: set(v) for k, v in old["bass"].items()}
    assert set(lex["instruments"]["drums"]) == set(old["drums"])
    old_tempo = {g["bpm"]: set(g["words"]) for g in old["tempo"]}
    new_tempo = {g["bpm"]: set(g["words"]) for g in lex["tempo"]["words"]}
    assert set(new_tempo) == set(old_tempo)
    added = {w for b in new_tempo for w in new_tempo[b] - old_tempo[b]}
    assert added == {"아다지오", "천천히", "모데라토", "안단테", "알레그로"}, "only the Korean tempo terms and 천천히 are new"
    # genre words come from the old drum-style table (or the old mood word lo-fi/lofi); hiphop words are the one requested addition
    old_style = {w for ws in old["style_words"].values() for w in ws} | {"lo-fi", "lofi"}
    for g in lex["genres"]["genres"]:
        assert set(g["words"]) <= old_style, g["id"]
    assert {w for u in lex["genres"]["unsupported"] for w in u["words"]} == {"hiphop", "hip-hop", "hip hop", "힙합"}


def test_the_lexicon_is_shipped_with_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "package-data" in text and "lexicon/*.json" in text


import pytest


@pytest.mark.parametrize("text,sig", [("6/8박자", "6/8"), ("빠른 록 기타 곡 6/8박자", "6/8"), ("3/4박자 왈츠풍", "3/4"), ("7/8박자로", "7/8"),
                                      ("rock song in 6/8", "6/8"), ("6/8 박자 곡", "6/8")])
def test_slash_meter_directly_followed_by_korean(text, sig):
    from prompt2midi.translate.extract import translate as extract
    assert extract(text).intent.time_signature == sig


@pytest.mark.parametrize("text", ["16/8 beats", "a6/8", "12/8박자", "6/80"])
def test_slash_meter_not_read_inside_longer_tokens(text):
    from prompt2midi.translate.extract import translate as extract
    assert extract(text).intent.time_signature in (None,)
