import json

import pytest

from prompt2midi.providers import FakeProvider, LLMProvider, ProviderError, RuleProvider


def test_fake_returns_responses_in_order_and_records_calls(spec_dict):
    fp = FakeProvider([spec_dict, "second"])
    assert fp.generate_spec("p1", {}, feedback=None, seed=3) is spec_dict
    assert fp.generate_spec("p2", {}, feedback=[{"path": "x", "message": "m"}]) == "second"
    assert [c.prompt for c in fp.calls] == ["p1", "p2"]
    assert fp.calls[0].feedback is None and fp.calls[0].seed == 3
    assert fp.calls[1].feedback == [{"path": "x", "message": "m"}]


def test_fake_runs_out(spec_dict):
    fp = FakeProvider([spec_dict])
    fp.generate_spec("p", {})
    with pytest.raises(ProviderError):
        fp.generate_spec("p", {})


def test_fake_repeat_last(spec_dict):
    fp = FakeProvider.fixed(spec_dict)
    assert [fp.generate_spec("p", {}) for _ in range(4)] == [spec_dict] * 4


def test_fake_raises_exception_items():
    fp = FakeProvider([ProviderError("down"), RuntimeError("boom")])
    with pytest.raises(ProviderError, match="down"):
        fp.generate_spec("p", {})
    with pytest.raises(RuntimeError, match="boom"):
        fp.generate_spec("p", {})


def test_fake_feedback_is_copied_not_aliased():
    fb = [{"path": "a", "message": "b"}]
    fp = FakeProvider(["x"])
    fp.generate_spec("p", {}, feedback=fb)
    fb[0]["message"] = "changed"
    assert fp.calls[0].feedback[0]["message"] == "b"


def test_fake_from_fixture(tmp_path, spec_dict):
    f = tmp_path / "rec.json"
    f.write_text(json.dumps([spec_dict, "not json {"]), encoding="utf-8")
    fp = FakeProvider.from_fixture(f)
    assert fp.generate_spec("p", {}) == spec_dict and fp.generate_spec("p", {}) == "not json {"
    f.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError):
        FakeProvider.from_fixture(f)


def test_providers_satisfy_the_protocol():
    assert isinstance(FakeProvider([]), LLMProvider) and isinstance(RuleProvider(), LLMProvider)
    assert RuleProvider().name == "rule" and FakeProvider([]).name == "fake"
