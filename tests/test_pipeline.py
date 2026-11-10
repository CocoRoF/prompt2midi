import copy
import json
import socket

import pytest

import prompt2midi.pipeline as P
from prompt2midi.compiler import CompileError
from prompt2midi.pipeline import GenerationError, generate
from prompt2midi.providers import FakeProvider, ProviderError, RuleProvider
from prompt2midi.validate import check_midi, validate_spec
from tests.conftest import VALID_SPEC

PROMPT = "A calm piano piece in C major, 70 BPM"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The whole pipeline must work offline: any socket use fails the test."""
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def bad(spec_dict, **changes):
    d = copy.deepcopy(spec_dict)
    d.update(changes)
    return d


def test_first_try_success(spec_dict):
    fp = FakeProvider.fixed(spec_dict)
    r = generate(PROMPT, provider=fp)
    assert r.provider_used == "fake" and not r.fallback_used
    assert [a.ok for a in r.attempts] == [True] and len(fp.calls) == 1 and fp.calls[0].feedback is None
    assert r.spec.title == "Night walk" and check_midi(r.midi, r.spec) == []
    assert r.seed == 12345  # from the spec


def test_invalid_json_then_valid_retries_with_feedback(spec_dict):
    fp = FakeProvider(["this is {not json", spec_dict])
    r = generate(PROMPT, provider=fp)
    assert len(fp.calls) == 2 and [a.ok for a in r.attempts] == [False, True]
    fb = fp.calls[1].feedback
    assert fb and fb[0]["path"] == "$" and "JSON" in fb[0]["message"]
    assert r.attempts[1].sent_feedback == fb and r.attempts[0].issues == fb
    assert not r.fallback_used


def test_schema_violation_feedback_names_the_field(spec_dict):
    fp = FakeProvider([bad(spec_dict, tempo_bpm=999), bad(spec_dict, key={"tonic": "H", "mode": "major"}), spec_dict])
    r = generate(PROMPT, provider=fp)
    assert len(fp.calls) == 3
    assert {"path": "tempo_bpm"}.items() <= {"path": fp.calls[1].feedback[0]["path"]}.items()
    assert any(f["path"] == "key.tonic" for f in fp.calls[2].feedback)
    assert [a.ok for a in r.attempts] == [False, False, True]


def test_theory_violation_is_fed_back(spec_dict):
    dup = copy.deepcopy(spec_dict)
    dup["sections"][1]["name"] = "A"
    fp = FakeProvider([dup, spec_dict])
    r = generate(PROMPT, provider=fp)
    assert fp.calls[1].feedback == [{"path": "sections[1].name", "message": "section name 'A' is used more than once"}]
    assert r.provider_used == "fake"


def test_provider_error_is_retried(spec_dict):
    fp = FakeProvider([ProviderError("rate limited"), spec_dict])
    r = generate(PROMPT, provider=fp)
    assert len(fp.calls) == 2 and "rate limited" in r.attempts[0].issues[0]["message"]


def test_unexpected_exception_never_escapes(spec_dict):
    fp = FakeProvider([RuntimeError("x" * 1000), spec_dict])
    r = generate(PROMPT, provider=fp)
    msg = r.attempts[0].issues[0]["message"]
    assert "RuntimeError" in msg and len(msg) < 400  # truncated


def test_non_dict_json_values_are_rejected_and_retried(spec_dict):
    fp = FakeProvider(["[1, 2, 3]", "null", None, 42, spec_dict])
    r = generate(PROMPT, provider=fp, max_retries=4)
    assert [a.ok for a in r.attempts] == [False] * 4 + [True]


def test_huge_response_is_refused(spec_dict):
    fp = FakeProvider(["x" * (P.MAX_RESPONSE_CHARS + 1), spec_dict])
    r = generate(PROMPT, provider=fp)
    assert "longer than" in r.attempts[0].issues[0]["message"]


def test_retries_exhausted_falls_back_to_rules(spec_dict):
    fp = FakeProvider.fixed("garbage")
    r = generate(PROMPT, provider=fp)  # default max_retries=2 -> 3 calls
    assert len(fp.calls) == 3
    assert r.provider_used == "rule" and r.fallback_used
    assert [(a.provider, a.ok) for a in r.attempts] == [("fake", False)] * 3 + [("rule", True)]
    assert r.spec.tempo_bpm == 70 and check_midi(r.midi, r.spec) == []  # the rule spec read "70 BPM"


def test_retries_exhausted_without_fallback_raises(spec_dict):
    fp = FakeProvider.fixed(bad(spec_dict, tempo_bpm=1))
    with pytest.raises(GenerationError) as e:
        generate(PROMPT, provider=fp, fallback=False)
    assert len(e.value.attempts) == 3 and all(not a.ok for a in e.value.attempts)
    assert "tempo_bpm" in str(e.value)


@pytest.mark.parametrize("retries,calls", [(0, 1), (1, 2), (2, 3), (5, 6)])
def test_retry_budget(retries, calls):
    fp = FakeProvider.fixed("nope")
    r = generate(PROMPT, provider=fp, max_retries=retries)
    assert len(fp.calls) == calls and r.fallback_used


def test_success_on_the_last_allowed_try(spec_dict):
    fp = FakeProvider(["a", "b", spec_dict])
    r = generate(PROMPT, provider=fp, max_retries=2)
    assert not r.fallback_used and len(fp.calls) == 3
    fp2 = FakeProvider(["a", "b", spec_dict])
    r2 = generate(PROMPT, provider=fp2, max_retries=1)  # only 2 tries, 3rd answer never asked for
    assert r2.fallback_used and len(fp2.calls) == 2


def test_failing_fallback_also_raises(spec_dict):
    primary, fallback = FakeProvider.fixed("bad", name="p"), FakeProvider.fixed("worse", name="f")
    with pytest.raises(GenerationError) as e:
        generate(PROMPT, provider=primary, fallback_provider=fallback)
    assert [a.provider for a in e.value.attempts] == ["p", "p", "p", "f"]


def test_rule_provider_as_primary_has_no_second_fallback():
    r = generate(PROMPT)  # provider=None -> RuleProvider
    assert r.provider_used == "rule" and not r.fallback_used and len(r.attempts) == 1


def test_custom_fallback_is_used(spec_dict):
    r = generate(PROMPT, provider=FakeProvider.fixed("x", name="p"), fallback_provider=FakeProvider.fixed(spec_dict, name="backup"))
    assert r.provider_used == "backup" and r.fallback_used


def test_compiler_refusal_is_treated_as_a_failed_try(spec_dict, monkeypatch):
    calls = []
    real = P.compile_spec

    def flaky(spec, seed=None):
        calls.append(1)
        if len(calls) == 1:
            raise CompileError("cannot compile", [])
        return real(spec, seed)
    monkeypatch.setattr(P, "compile_spec", flaky)
    fp = FakeProvider.fixed(spec_dict)
    r = generate(PROMPT, provider=fp)
    assert len(fp.calls) == 2 and fp.calls[1].feedback == [{"path": "$", "message": "cannot compile"}]


def test_broken_midi_is_never_returned(spec_dict, monkeypatch):
    real = P.compile_spec
    monkeypatch.setattr(P, "compile_spec", lambda spec, seed=None: real(spec, seed)[:-9])  # truncated file
    fp = FakeProvider.fixed(spec_dict)
    with pytest.raises(GenerationError) as e:
        generate(PROMPT, provider=fp)
    assert any("generated MIDI is invalid" in i["message"] for a in e.value.attempts for i in a.issues)
    assert len(e.value.attempts) == 4  # 3 primary tries + the rule fallback, all rejected


# ---------- reproducibility ----------

def test_same_input_same_bytes_with_a_deterministic_provider(spec_dict):
    a = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    b = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    assert a.midi == b.midi and a.seed == b.seed


def test_same_prompt_same_bytes_with_the_rule_provider():
    assert generate(PROMPT).midi == generate(PROMPT).midi
    assert generate(PROMPT).midi != generate("A sad song in A minor").midi


def test_fallback_result_is_reproducible():
    one = generate(PROMPT, provider=FakeProvider.fixed("x"))
    two = generate(PROMPT, provider=FakeProvider.fixed("y"))
    assert one.midi == two.midi and one.fallback_used and two.fallback_used


def test_request_seed_overrides_and_is_reported(spec_dict):
    base = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    s1 = generate(PROMPT, provider=FakeProvider.fixed(spec_dict), seed=1)
    s1b = generate(PROMPT, provider=FakeProvider.fixed(spec_dict), seed=1)
    s2 = generate(PROMPT, provider=FakeProvider.fixed(spec_dict), seed=2)
    assert s1.seed == 1 and s1.midi == s1b.midi and s1.midi != s2.midi and s1.midi != base.midi
    fp = FakeProvider.fixed(spec_dict)
    generate(PROMPT, provider=fp, seed=9)
    assert fp.calls[0].seed == 9  # forwarded to the provider too


def test_derived_seed_when_spec_has_none(spec_dict):
    del spec_dict["seed"]
    a = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    assert a.seed == generate(PROMPT, provider=FakeProvider.fixed(spec_dict)).seed


def test_replay_from_a_recorded_fixture(tmp_path, spec_dict):
    f = tmp_path / "session.json"
    f.write_text(json.dumps(["{broken", bad(spec_dict, tempo_bpm=0), spec_dict]), encoding="utf-8")
    r = generate(PROMPT, provider=FakeProvider.from_fixture(f))
    assert [a.ok for a in r.attempts] == [False, False, True]


def test_warnings_are_returned(spec_dict):
    spec_dict["sections"][0]["chords"] = ["C", "F#7"]  # not diatonic to C minor
    r = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    assert [w.code for w in r.warnings] == ["non_diatonic", "non_diatonic"]


# ---------- input checks ----------

@pytest.mark.parametrize("prompt", ["", "   \n", None, 5])
def test_bad_prompts(prompt):
    with pytest.raises(ValueError):
        generate(prompt)


def test_prompt_too_long_and_bad_options():
    with pytest.raises(ValueError):
        generate("x" * (P.MAX_PROMPT_CHARS + 1))
    with pytest.raises(ValueError):
        generate(PROMPT, max_retries=-1)
    for seed in (-1, 2**32):
        with pytest.raises(ValueError):
            generate(PROMPT, seed=seed)


def test_pipeline_result_validates_again_independently(spec_dict):
    r = generate(PROMPT, provider=FakeProvider.fixed(spec_dict))
    spec, report = validate_spec(r.spec.model_dump(mode="json", by_alias=True))
    assert spec is not None and report.ok
    assert VALID_SPEC["title"] == r.spec.title
