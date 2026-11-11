"""RuleProvider: Korean key / meter / genre / tempo words.

Fixed here: explicit keys and time signatures in Korean (tonic+mode and meter are already fields of MusicSpec).
NOT fixed here (xfail, strict): genre -> drum style / instruments. The genre vocabulary waits for the shared
Intent v1 schema (genre enum); the xfail tests record the wanted behaviour and will fail loudly (XPASS strict)
as soon as a fix makes them pass, so the marker must then be removed.
"""
import pytest

from prompt2midi.compiler import compile_spec
from prompt2midi.providers import RuleProvider
from prompt2midi.validate import check_midi, validate_spec

RULE = RuleProvider()


def spec_of(prompt):
    d = RULE.generate_spec(prompt)
    spec, report = validate_spec(d)
    assert spec is not None and report.ok, (prompt, [i.as_dict() for i in report.issues])
    assert check_midi(compile_spec(spec), spec) == [], prompt
    return spec


def key_of(prompt):
    s = spec_of(prompt)
    return s.key.tonic.value, s.key.mode.value


def drum_style(prompt):
    s = spec_of(prompt)
    return next((t.style.value for t in s.tracks if t.role.value == "drums"), None)


# ---------------- keys (fixed) ----------------

@pytest.mark.parametrize("text,expected", [
    ("A단조", ("A", "minor")), ("C장조", ("C", "major")), ("내림나장조", ("Bb", "major")), ("올림바단조", ("F#", "minor")),
    ("다장조", ("C", "major")), ("라단조", ("D", "minor")), ("마장조", ("E", "major")), ("사장조", ("G", "major")), ("가단조", ("A", "minor")),
    ("바장조", ("F", "major")), ("나단조", ("B", "minor")), ("내림마장조", ("Eb", "major")), ("올림다단조", ("C#", "minor")),
    ("내림라장조", ("Db", "major")), ("올림사단조", ("G#", "minor")), ("내림가장조", ("Ab", "major")), ("내림 나 장조", ("Bb", "major")),
    ("F샵 단조", ("F#", "minor")), ("B플랫 장조", ("Bb", "major")), ("Bb장조", ("Bb", "major")), ("C# 단조", ("C#", "minor")),
    ("내림B장조", ("Bb", "major")), ("올림F단조", ("F#", "minor")), ("a단조", ("A", "minor")),
    ("D 도리안", ("D", "dorian")), ("라 도리안", ("D", "dorian")), ("G 믹솔리디안", ("G", "mixolydian")), ("F 리디안", ("F", "lydian")),
    ("E 프리지안", ("E", "phrygian")), ("A 화성단조", ("A", "harmonic_minor")), ("A 하모닉 마이너", ("A", "harmonic_minor")),
    ("A minor", ("A", "minor")), ("B-flat major", ("Bb", "major")),
], ids=str)
def test_explicit_keys_are_read(text, expected):
    assert key_of(f"피아노 곡 {text} 로 연주") == expected
    assert key_of(text) == expected


def test_the_reported_case_a_minor_is_not_ignored():
    s = spec_of("슬프고 어두운 현악 앙상블, A단조")
    assert (s.key.tonic.value, s.key.mode.value) == ("A", "minor"), "used to give E phrygian"
    why = RULE.explain("슬프고 어두운 현악 앙상블, A단조")
    assert why["key"] == "A minor" and not any(d.startswith("key=") for d in why["defaults"])


# ---------------- key priority rules ----------------

def test_explicit_key_beats_the_mood_mode():
    # 'dark' prefers phrygian, 'happy' prefers major: an explicit key wins either way
    assert RULE.explain("어두운 음악 C장조")["key"] == "C major" and RULE.explain("어두운 음악 C장조")["mood"] == "dark"
    assert RULE.explain("행복한 A단조")["key"] == "A minor" and RULE.explain("행복한 A단조")["mood"] == "happy"
    assert RULE.explain("A minor, a cheerful song")["key"] == "A minor"


def test_explicit_key_does_not_change_the_mood_driven_parts():
    plain, keyed = RULE.generate_spec("행복한 피아노 곡"), RULE.generate_spec("행복한 피아노 곡 A단조")
    assert plain["tempo_bpm"] == keyed["tempo_bpm"] and plain["sections"][0]["energy"] == keyed["sections"][0]["energy"]
    assert plain["tracks"] == keyed["tracks"] and plain["key"] != keyed["key"]
    assert keyed["key"] == {"tonic": "A", "mode": "minor"}


def test_chords_follow_the_explicit_key():
    d = RULE.generate_spec("행복한 A단조")
    assert d["sections"][0]["chords"] == ["Am", "F", "C", "G"]  # i VI III VII of A minor


def test_a_mode_word_without_tonic_beats_the_mood_and_uses_the_default_tonic():
    assert RULE.explain("행복한 도리안 느낌")["key"] == "D dorian"
    assert RULE.explain("행복한 단조 곡")["key"] == "A minor"
    assert RULE.explain("슬픈 장조 곡")["key"] == "C major"
    assert RULE.explain("a happy dorian tune")["key"] == "D dorian"


def test_without_any_key_or_mode_word_the_mood_decides():
    assert RULE.explain("슬프고 어두운 현악 앙상블")["key"] == "E phrygian"
    assert RULE.explain("hello")["key"] == "C major"


def test_first_explicit_key_wins_when_several_are_given():
    assert RULE.explain("C장조로 시작해서 A단조로 끝나는")["key"] == "C major"
    assert RULE.explain("A단조에서 C장조로")["key"] == "A minor"
    assert RULE.explain("in D minor then C장조")["key"] == "D minor"
    assert RULE.explain("C장조 then in D minor")["key"] == "C major"


def test_monotonous_is_not_minor():
    for text in ("단조로운 멜로디", "단조롭지만 편안한", "단조로워서 좋은"):
        why = RULE.explain(text)
        assert not any("mode word" in d for d in why["defaults"]), text  # the key comes from the mood, not from the word 단조


def test_impossible_spellings_are_ignored_not_guessed():
    # 내림다장조 would be Cb major, which the closed tonic list does not contain: falls back to the mood default
    why = RULE.explain("내림다장조")
    assert why["key"] == "C major" and any(d.startswith("key=") for d in why["defaults"])
    assert key_of("올림미장조") == ("C", "major")  # '미' is not a letter name here (마 = E)


def test_korean_letters_inside_words_are_not_keys():
    from prompt2midi.providers.rule import find_key
    assert find_key("연마단조") is None and find_key("연주 사랑 노래") is None and find_key("바다 소리") is None
    assert find_key("피아노 곡 마단조") == ("E", "minor")
    assert find_key("마단조 아님 연마장조") == ("E", "minor")


# ---------------- time signatures (fixed) ----------------

@pytest.mark.parametrize("text,sig", [
    ("4분의 3박자", [3, 4]), ("8분의 6박자", [6, 8]), ("4분의 4박자", [4, 4]), ("4분의 2박자", [2, 4]), ("4분의 5박자", [5, 4]), ("8분의 7박자", [7, 8]),
    ("사분의 삼박자", [3, 4]), ("팔분의 육박자", [6, 8]), ("팔분의 칠박자", [7, 8]), ("4 분의 3 박자", [3, 4]), ("4분의3박자", [3, 4]),
    ("3박자", [3, 4]), ("2박자", [2, 4]), ("4박자", [4, 4]), ("5박자", [5, 4]), ("삼박자", [3, 4]),
    ("3/4", [3, 4]), ("6/8 박자", [6, 8]), ("7/8", [7, 8]),
])
def test_korean_time_signatures(text, sig):
    assert RULE.generate_spec(f"피아노 곡, {text}")["time_signature"] == sig
    assert spec_of(f"피아노 곡, {text}").time_signature == tuple(sig)


@pytest.mark.parametrize("text", ["9분의 8박자", "2분의 3박자", "8분의 3박자", "12분의 8박자", "6박자", "7박자", "10박자"])
def test_unsupported_meters_fall_back_to_four_four(text):
    assert RULE.generate_spec(f"피아노 곡, {text}")["time_signature"] == [4, 4]


def test_meter_and_key_together():
    d = RULE.generate_spec("A단조 4분의 3박자 피아노 곡")
    assert d["key"] == {"tonic": "A", "mode": "minor"} and d["time_signature"] == [3, 4]
    s = spec_of("내림나장조, 8분의 6박자, 느리게")
    assert (s.key.tonic.value, s.time_signature, s.tempo_bpm) == ("Bb", (6, 8), 66)


def test_numbers_in_other_roles_are_not_meters():
    assert RULE.generate_spec("120 BPM 피아노 곡")["time_signature"] == [4, 4]
    assert RULE.generate_spec("3분짜리 곡")["time_signature"] == [4, 4]
    assert RULE.generate_spec("2024년 4월 5박스")["time_signature"] == [4, 4]


# ---------------- tempo words ----------------

def test_tempo_words_that_work_today():
    assert RULE.generate_spec("신나는 느리게 연주")["tempo_bpm"] == 66
    assert RULE.generate_spec("슬픈 빠른 템포의 곡")["tempo_bpm"] == 140
    assert RULE.generate_spec("슬픈 곡 빠르게")["tempo_bpm"] == 140
    assert RULE.generate_spec("슬픈 곡 아주 느린")["tempo_bpm"] == 55
    assert RULE.generate_spec("슬픈 곡 보통 빠르기")["tempo_bpm"] == 96
    assert RULE.generate_spec("calm piece 90 BPM")["tempo_bpm"] == 90


@pytest.mark.xfail(strict=True, reason="Korean/Italian tempo terms (아다지오, 알레그로, 모데라토, 안단테) are not in TEMPO_WORDS yet; tempo vocabulary is not part of this change")
@pytest.mark.parametrize("text,lo,hi", [("신나는 아다지오", 55, 80), ("슬픈 알레그로", 110, 140), ("슬픈 모데라토", 90, 110), ("신나는 안단테", 70, 100)])
def test_italian_tempo_terms_in_korean_script(text, lo, hi):
    assert lo <= RULE.generate_spec(text)["tempo_bpm"] <= hi


@pytest.mark.xfail(strict=True, reason="'느릿느릿', '천천히' style adverbs are not in TEMPO_WORDS")
def test_other_slow_words():
    assert RULE.generate_spec("신나는 천천히 연주")["tempo_bpm"] <= 76


# ---------------- genre (NOT fixed: xfail until the Intent v1 genre enum is agreed) ----------------

GENRE_XFAIL = "genre words only matter when a drum track is already requested (drum word or high-energy mood); genre vocabulary waits for the Intent v1 genre enum"


@pytest.mark.xfail(strict=True, reason=GENRE_XFAIL)
@pytest.mark.parametrize("text,style", [
    ("비 오는 날 창가에서 듣는 재즈", "shuffle"), ("재즈", "shuffle"), ("잔잔한 재즈 피아노", "shuffle"),
    ("록 음악", "rock_basic"), ("잔잔한 발라드", "half_time"), ("보사노바 기타", "bossa"), ("a quiet jazz piece", "shuffle"),
])
def test_genre_is_reflected_without_asking_for_drums(text, style):
    assert drum_style(text) == style


@pytest.mark.parametrize("text,style", [
    ("재즈 트리오 드럼 포함", "shuffle"), ("록 밴드 드럼", "rock_basic"), ("발라드 드럼", "half_time"), ("보사노바 드럼", "bossa"),
    ("재즈 느낌의 드럼 비트", "shuffle"),
])
def test_genre_is_reflected_when_drums_are_requested(text, style):
    """What works today: the genre word picks the drum STYLE once a drum track exists."""
    assert drum_style(text) == style


@pytest.mark.xfail(strict=True, reason="'왈츠'/'waltz' implies 3/4 but meter-from-genre inference is not implemented (genre vocabulary pending)")
def test_waltz_implies_three_four():
    assert RULE.generate_spec("우아한 왈츠")["time_signature"] == [3, 4]


# ---------------- invariants ----------------

@pytest.mark.parametrize("text", [
    "슬프고 어두운 현악 앙상블, A단조", "비 오는 날 창가에서 듣는 재즈", "내림나장조 4분의 3박자 로맨틱한 발라드", "올림바단조 록 드럼",
    "라 도리안 8분의 6박자", "단조 단조 장조 3박자 4분의 5박자", "내림 내림 올림 올림", "마마마마 가가가 4분의 4분의", "A단조" * 300,
])
def test_korean_prompts_always_give_a_valid_compilable_spec(text):
    spec_of(text)


def test_korean_key_and_meter_are_deterministic():
    a = compile_spec(spec_of("슬프고 어두운 현악 앙상블, A단조"))
    assert a == compile_spec(spec_of("슬프고 어두운 현악 앙상블, A단조"))


# ---------------- false positives: words that merely contain a note name + 장조/단조 ----------------

WRONG_READING = {
    "사장조직 회의 음악": "G major", "마장조선시대 음악": "E major", "나 장조림 요리 노래": "B major", "가 장조 심": "A major", "바 단조건": "F minor",
}


@pytest.mark.parametrize("text,wrong", list(WRONG_READING.items()), ids=list(WRONG_READING))
def test_reported_false_positives_are_no_longer_keys(text, wrong):
    from prompt2midi.providers.rule import find_key
    assert find_key(text) is None, text
    assert RULE.explain(text)["key"] != wrong, text
    spec_of(text)


@pytest.mark.parametrize("text", [
    "다장조림", "라단조건물", "가단조건", "마장조선", "사단조직", "바장조림 요리", "나단조건 없이", "다 단조건", "가 장조림", "라 장조 심사",
    "마 단조 건물", "a 단조 곡", "라 단조 피아노", "나 장조 요리",
])
def test_more_words_containing_a_note_name_and_a_mode_are_not_keys(text):
    from prompt2midi.providers.rule import find_key
    assert find_key(text) is None, text


@pytest.mark.parametrize("text,expected", [
    ("C장조에서", ("C", "major")), ("A단조로", ("A", "minor")), ("라단조 피아노", ("D", "minor")), ("C장조의 밝은 피아노 곡", ("C", "major")),
    ("C장조는 밝다", ("C", "major")), ("A단조를 쓴 곡", ("A", "minor")), ("A단조곡", ("A", "minor")), ("마장조풍의 노래", ("E", "major")),
    ("D장조처럼 밝게", ("D", "major")), ("G장조와 E단조", ("G", "major")), ("C장조입니다", ("C", "major")), ("F장조도 좋다", ("F", "major")),
    ("A단조에서는", ("A", "minor")), ("C장조으로", ("C", "major")), ("바단조로서", ("F", "minor")), ("A단조 스타일", ("A", "minor")),
    ("올림바단조 록", ("F#", "minor")), ("내림 나 장조", ("Bb", "major")), ("A 단조", ("A", "minor")), ("D 도리안", ("D", "dorian")), ("라 도리안", ("D", "dorian")),
    ("다장조.", ("C", "major")), ("다장조,", ("C", "major")), ("(라단조)", ("D", "minor")), ("곡: 사장조", ("G", "major")),
], ids=str)
def test_legitimate_forms_still_work(text, expected):
    from prompt2midi.providers.rule import find_key
    assert find_key(text) == expected


def test_a_real_key_inside_a_sentence_is_found_and_a_lookalike_before_it_is_skipped():
    from prompt2midi.providers.rule import find_key
    assert find_key("사장조직 회의 음악이지만 곡은 A단조") == ("A", "minor")
    assert find_key("마장조선시대 이야기, 라단조 피아노") == ("D", "minor")


def test_false_positive_does_not_change_the_mood_driven_result():
    plain = RULE.generate_spec("회의 음악")
    for text in ("사장조직 회의 음악", "바 단조건 회의 음악"):
        d = RULE.generate_spec(text)
        assert d["key"] == plain["key"] and d["tempo_bpm"] == plain["tempo_bpm"], text


def test_known_limitation_adjacent_note_name_and_mode_followed_by_a_space_is_a_key():
    """'사장조 직원' (president + staff) is indistinguishable from '라단조 피아노' without a dictionary: it is read as G major."""
    from prompt2midi.providers.rule import find_key
    assert find_key("사장조 직원") == ("G", "major")
