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
         "expect": {"tonic": "F#", "mode": "minor", "tempo_bpm": 120, "time_signature": "3/4", "bars": 16, "instruments": ["violin"]}},
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


# ---------- metrics added for the 연구 1팀 item map (docs/eval-metrics.md section 3) ----------

import io

import mido

from prompt2midi.evalkit import metrics as M
from prompt2midi.spec import MusicSpec


def _midi(tracks, ppq=480):
    """tracks: list of event lists [(abs_tick, mido.Message|MetaMessage)] -> bytes. Track 0 carries tempo and meter."""
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    for evs in tracks:
        tr = mido.MidiTrack()
        t = 0
        for tick, msg in sorted(evs, key=lambda e: e[0]):
            tr.append(msg.copy(time=tick - t))
            t = tick
        tr.append(mido.MetaMessage("end_of_track", time=0))
        mid.tracks.append(tr)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _ctx(midi, expect=None):
    spec = MusicSpec.model_validate(runner.FAKE_SPEC)
    return M.Context(model.Expect.model_validate(expect) if expect else None, spec, midi)


META = [(0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100))), (0, mido.MetaMessage("time_signature", numerator=4, denominator=4))]


def _notes(channel, program, pitches):
    ev = [(0, mido.Message("program_change", channel=channel, program=program))]
    for i, p in enumerate(pitches):
        ev += [(i * 480, mido.Message("note_on", channel=channel, note=p, velocity=80)), (i * 480 + 240, mido.Message("note_off", channel=channel, note=p, velocity=0))]
    return ev


def test_pitch_range_uses_the_instrument_of_the_channel():
    ok = _midi([META, _notes(0, 0, [60, 62, 64, 65])])                      # piano 21..108
    assert M.pitch_range(_ctx(ok)).status == "pass"
    low = _midi([META, _notes(0, 40, [40, 41, 60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84, 86, 88, 90])])  # violin >= 55: 2 of 20 = 10%
    r = M.pitch_range(_ctx(low))
    assert r.status == "fail" and "2/20" in r.reason
    drums_only = _midi([META, _notes(9, 0, [36, 38])])
    assert M.pitch_range(_ctx(drums_only)).status == "na"


def test_note_integrity_counts_zero_length_and_unpaired_notes():
    assert M.note_integrity(_ctx(_midi([META, _notes(0, 0, [60, 62])]))).status == "pass"
    zero = [(0, mido.Message("note_on", channel=0, note=60, velocity=80)), (0, mido.Message("note_off", channel=0, note=60, velocity=0))]
    assert M.note_integrity(_ctx(_midi([META, zero]))).status == "fail"      # MidiCheck only warns about this
    hanging = [(0, mido.Message("note_on", channel=0, note=60, velocity=80))]
    r = M.note_integrity(_ctx(_midi([META, hanging])))
    assert r.status == "fail" and "never turned off" in r.reason


def test_no_empty_tracks_ignores_the_meta_track_only():
    assert M.no_empty_tracks(_ctx(_midi([META, _notes(0, 0, [60])]))).status == "pass"
    r = M.no_empty_tracks(_ctx(_midi([META, _notes(0, 0, [60]), [(0, mido.Message("program_change", channel=1, program=33))]])))
    assert r.status == "fail" and "1개" in r.reason


def test_midi_instruments_included_reads_program_changes_and_drum_channel():
    midi = _midi([META, _notes(0, 0, [60]), _notes(1, 33, [40]), _notes(9, 0, [36])])
    assert M.midi_instruments_included(_ctx(midi, {"instruments": ["piano", "electric_bass", "drums"]})).status == "pass"
    r = M.midi_instruments_included(_ctx(midi, {"instruments": ["piano", "violin"]}))
    assert r.status == "fail" and "violin" in r.reason
    assert M.midi_instruments_included(_ctx(midi)).status == "na"
    # a track whose program is piano but that has no notes does not count as 'present'
    assert M.midi_instruments_included(_ctx(_midi([META, _notes(0, 0, [60]), [(0, mido.Message("program_change", channel=2, program=40))]]), {"instruments": ["violin"]})).status == "fail"


def test_scale_deviation_uses_the_labelled_key_and_needs_both_labels():
    c_major = _midi([META, _notes(0, 0, [60, 62, 64, 65, 67, 69, 71, 72, 74, 76])])
    assert M.scale_deviation(_ctx(c_major, {"tonic": "C", "mode": "major"})).status == "pass"
    # D minor = D E F G A Bb C: the single B of 10 notes is outside -> exactly 10% is still allowed (<=)
    assert M.scale_deviation(_ctx(c_major, {"tonic": "D", "mode": "minor"})).status == "pass"
    more_b = _midi([META, _notes(0, 0, [60, 62, 64, 65, 67, 69, 71, 71, 74, 76])])  # two Bs -> 20%
    r = M.scale_deviation(_ctx(more_b, {"tonic": "D", "mode": "minor"}))
    assert r.status == "fail" and "2/10" in r.reason
    assert M.scale_deviation(_ctx(c_major, {"tonic": "C"})).status == "na"
    assert M.scale_deviation(_ctx(c_major)).status == "na"
    assert M.scale_deviation(_ctx(_midi([META, _notes(9, 0, [36, 38])]), {"tonic": "C", "mode": "major"})).status == "na"  # drums are not pitched


def test_new_metrics_in_the_five_item_set(tmp_path):
    r = run5(tmp_path)
    assert st(r, "p1", "scale_deviation") == "pass" and st(r, "p2", "scale_deviation") == "fail"
    assert st(r, "p4", "scale_deviation") == "na" and st(r, "p3", "scale_deviation") == "na"   # only one label / none
    assert st(r, "p1", "midi_instruments_included") == "pass" and st(r, "p2", "midi_instruments_included") == "fail"
    assert all(st(r, i, m) == "pass" for i in r for m in ("pitch_range", "note_integrity", "no_empty_tracks"))
