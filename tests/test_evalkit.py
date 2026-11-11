import copy
import json
import re
from pathlib import Path

import pytest

from prompt2midi import evalkit
from prompt2midi.evalkit import __main__ as cli
from prompt2midi.evalkit import model, report, runner
from prompt2midi.evalkit.metrics import METRICS, LISTEN_COLUMNS
from prompt2midi.providers import FakeProvider
from prompt2midi.providers.base import ProviderError

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = json.loads((ROOT / "data" / "eval_prompts_research1.json").read_text(encoding="utf-8"))

# The fake provider always answers FAKE_SPEC: C major, 100 BPM, 4/4, 12 bars, piano + electric bass + drums.
SET5 = {
    "version": "1", "name": "five",
    "prompts": [
        {"id": "p1", "lang": "en", "tier": "A", "text": "labels all right",
         "expect": {"tonic": "C", "mode": "major", "tempo_bpm": 100, "time_signature": "4/4", "bars": 12, "instruments": ["piano", "drums"]}},
        {"id": "p2", "lang": "ko", "tier": "A", "text": "라벨이 모두 틀림",
         "expect": {"tonic": "D", "mode": "minor", "tempo_bpm": 120, "time_signature": "3/4", "bars": 16, "instruments": ["violin"]}},
        {"id": "p3", "lang": "en", "tier": "B", "text": "no labels at all"},
        {"id": "p4", "lang": "ko", "tier": "B", "text": "라벨 일부만", "expect": {"tonic": "C"}},
        {"id": "p5", "lang": "ko", "tier": "C", "text": "네 마디만", "expect": {"bars": 4}},
    ],
}


def write(tmp_path, data, name="set.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def run5(tmp_path, data=SET5, provider="fake", **kw):
    s, _ = evalkit.load_set(write(tmp_path, data))
    return {r.id: r for r in runner.run_set(s, provider, **kw)}


def st(res, item, metric):
    return res[item].results[metric].status


# ---------- per-metric pass / fail / na ----------

def test_metrics_pass_fail_na_cases(tmp_path):
    r = run5(tmp_path)
    for m in ("tempo_match", "key_match", "time_signature_match", "bars_match", "instruments_included"):
        assert st(r, "p1", m) == "pass", m
        assert st(r, "p2", m) == "fail", m
        assert st(r, "p3", m) == "na", m
    # partial labels: only the tonic is labelled -> key_match decides, the others are na
    assert st(r, "p4", "key_match") == "pass"
    assert [st(r, "p4", m) for m in ("tempo_match", "time_signature_match", "bars_match", "instruments_included")] == ["na"] * 4
    # length rule: applies only without a bars label (fake spec has 12 bars -> pass)
    assert [st(r, i, "bars_min") for i in ("p1", "p2", "p3", "p4", "p5")] == ["na", "na", "pass", "pass", "na"]
    assert st(r, "p5", "bars_match") == "fail" and "12" in r["p5"].results["bars_match"].reason
    assert all(st(r, i, "midicheck_valid") == "pass" and st(r, i, "deterministic") == "pass" for i in r)
    assert "100.00 BPM" in r["p1"].results["tempo_match"].reason and "빠진 악기 ['violin']" in r["p2"].results["instruments_included"].reason


def test_key_match_treats_enharmonic_names_as_equal(tmp_path, monkeypatch):
    spec = copy.deepcopy(runner.FAKE_SPEC)
    spec["key"] = {"tonic": "Db", "mode": "major"}
    monkeypatch.setattr(runner, "FAKE_SPEC", spec)
    data = {"version": "1", "name": "enh", "prompts": [{"id": "e", "lang": "en", "tier": "A", "text": "x", "expect": {"tonic": "C#", "mode": "major"}}]}
    assert st(run5(tmp_path, data), "e", "key_match") == "pass"


def test_bars_min_flags_pieces_shorter_than_eight_bars(tmp_path, monkeypatch):
    spec = copy.deepcopy(runner.FAKE_SPEC)
    spec["sections"] = [{"name": "A", "bars": 4, "chords": ["C", "G"], "energy": 0.5}]
    monkeypatch.setattr(runner, "FAKE_SPEC", spec)
    r = run5(tmp_path)
    assert st(r, "p3", "bars_min") == "fail" and "최소 8" in r["p3"].results["bars_min"].reason
    assert st(r, "p5", "bars_match") == "pass"  # an explicit '4 bars' prompt is judged by the label, not the minimum


def test_generation_failure_counts_as_fail_not_na(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "make_provider", lambda name: FakeProvider([ProviderError("boom")] * 3, name="fake"))
    r = run5(tmp_path)
    assert r["p1"].generated is False and r["p1"].attempts == 3 and "boom" in r["p1"].error
    assert st(r, "p1", "tempo_match") == "fail" and "생성 실패" in r["p1"].results["tempo_match"].reason
    assert st(r, "p3", "tempo_match") == "na"          # nothing was asked of it
    assert st(r, "p3", "midicheck_valid") == "fail" and st(r, "p3", "bars_min") == "fail"
    assert st(r, "p3", "deterministic") == "na"


def test_retries_and_fallback_are_reported(tmp_path, monkeypatch):
    good = copy.deepcopy(runner.FAKE_SPEC)
    monkeypatch.setattr(runner, "make_provider", lambda name: FakeProvider(["not json", good], name="fake"))
    r = run5(tmp_path, check_determinism=False)
    assert r["p1"].generated and r["p1"].attempts == 2 and r["p1"].fallback_used is False
    monkeypatch.setattr(runner, "make_provider", lambda name: FakeProvider([ProviderError("x")], name="fake", repeat_last=True))
    r = run5(tmp_path, fallback=True, max_retries=0, check_determinism=False)
    assert r["p1"].fallback_used is True and r["p1"].provider_used == "rule"


# ---------- set format ----------

BAD = {
    "missing field": lambda d: d["prompts"][0].pop("text"),
    "unknown lang": lambda d: d["prompts"][0].update(lang="fr"),
    "unknown tier": lambda d: d["prompts"][0].update(tier="D"),
    "extra field": lambda d: d["prompts"][0].update(mood="calm"),
    "extra expect field": lambda d: d["prompts"][0].update(expect={"mood": "calm"}),
    "tempo out of range": lambda d: d["prompts"][0].update(expect={"tempo_bpm": 5}),
    "bad signature": lambda d: d["prompts"][0].update(expect={"time_signature": "4-4"}),
    "bad instrument": lambda d: d["prompts"][0].update(expect={"instruments": ["kazoo"]}),
    "bad tonic": lambda d: d["prompts"][0].update(expect={"tonic": "H"}),
    "duplicate id": lambda d: d["prompts"].append(copy.deepcopy(d["prompts"][0])),
    "bad id": lambda d: d["prompts"][0].update(id="has space"),
    "empty prompts": lambda d: d.update(prompts=[]),
    "wrong version": lambda d: d.update(version="2"),
}


@pytest.mark.parametrize("name", list(BAD))
def test_invalid_sets_are_rejected(tmp_path, name):
    data = copy.deepcopy(SET5)
    BAD[name](data)
    with pytest.raises(evalkit.SetError) as e:
        evalkit.load_set(write(tmp_path, data))
    assert e.value.problems
    assert cli.main([str(write(tmp_path, data)), "--provider", "fake", "--out", str(tmp_path / "o")]) == 2
    assert not (tmp_path / "o").exists()


def test_non_json_and_missing_file_are_rejected(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(evalkit.SetError):
        evalkit.load_set(p)
    assert cli.main([str(p), "--provider", "fake"]) == 2
    assert cli.main([str(tmp_path / "missing.json"), "--provider", "fake"]) == 2


def test_committed_json_schema_matches_models():
    committed = (ROOT / "schemas" / "evalset.v1.schema.json").read_text(encoding="utf-8")
    assert committed == model.json_schema_text(), (
        "stale schemas/evalset.v1.schema.json: python -c \"from prompt2midi.evalkit.model import json_schema_text as t; "
        "open('schemas/evalset.v1.schema.json','w',encoding='utf-8').write(t())\"")


def test_holdout_files_are_ignored_by_git():
    assert "data/eval/holdout*" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


# ---------- outputs ----------

def test_summary_denominators_exclude_na(tmp_path):
    out = tmp_path / "out"
    assert cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)]) == 0
    md = (out / "summary.md").read_text(encoding="utf-8")
    line = next(l for l in md.splitlines() if l.startswith("| 템포 일치"))
    assert line.endswith("| 1/2 (50%) · na 3 |"), line       # p1 pass, p2 fail, p3-p5 na
    assert "분모는 pass+fail 만" in md and "청취 필요" in md
    line = next(l for l in md.splitlines() if l.startswith("| 마디 수 일치"))
    assert line.endswith("| 1/3 (33%) · na 2 |"), line       # p1 pass, p2 fail, p5 fail
    assert "- `p2` · `tempo_match`:" in md
    # tier x language columns exist
    assert "A·en" in md and "A·ko" in md and "층 B 전체" in md and "ko 전체" in md


def test_csv_columns_and_listening_columns(tmp_path):
    out = tmp_path / "out"
    cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)])
    import csv
    rows = list(csv.DictReader(open(out / "results.csv", encoding="utf-8")))
    assert [r["id"] for r in rows] == ["p1", "p2", "p3", "p4", "p5"]
    assert list(rows[0]) == report.columns()
    for m in METRICS:
        assert m.name in rows[0] and m.name + "_reason" in rows[0]
    assert all(r[c] == "청취 필요" for r in rows for c in LISTEN_COLUMNS)
    assert "mood_fit" not in rows[0] and not any("score" in k for k in rows[0])


@pytest.mark.parametrize("provider", ["fake", "rule"])
def test_same_code_same_set_gives_identical_files(tmp_path, provider):
    s = write(tmp_path, SET5)
    for d in ("a", "b"):
        assert cli.main([str(s), "--provider", provider, "--out", str(tmp_path / d)]) == 0
    for f in ("results.csv", "summary.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_llm_provider_is_not_run(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("llm must not run anything")
    monkeypatch.setattr(runner, "run_set", boom)
    monkeypatch.setattr(cli, "run_set", boom)
    out = tmp_path / "out"
    assert cli.main([str(write(tmp_path, SET5)), "--provider", "llm", "--out", str(out)]) == 0
    assert "미실행(키 없음)" in capsys.readouterr().out
    assert not out.exists()
    with pytest.raises(ValueError):
        runner.make_provider("llm")


def test_rule_provider_on_the_research_prompts(tmp_path):
    """The 10 research prompts spell out BPM, key and the time signature (one is 3/4): the rule provider should read all of them."""
    prompts = []
    for p in RESEARCH:
        tonic, mode = p["key"].split(" ", 1)
        sig = re.search(r"(\d/\d) time signature", p["text"]).group(1)
        prompts.append({"id": f"r{p['id']}", "lang": "en", "tier": "A", "text": p["text"],
                        "expect": {"tonic": tonic, "mode": mode, "tempo_bpm": p["bpm"], "time_signature": sig}})
    r = run5(tmp_path, {"version": "1", "name": "research10", "prompts": prompts}, provider="rule")
    for m in ("tempo_match", "key_match", "time_signature_match", "midicheck_valid", "deterministic", "bars_min"):
        assert [r[i].results[m].status for i in r] == ["pass"] * 10, m
