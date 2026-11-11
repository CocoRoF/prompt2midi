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
    line = next(l for l in md.splitlines() if l.startswith("| 마디 수 선언 일치"))
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


# ---------- 연구 1팀 검토 회신 반영: --tempo-tol, 새 열, 축 1 집계, 요약 머리 ----------

from prompt2midi.evalkit.version import EVALKIT_VERSION

V01_COLUMNS = (  # the columns of PR #21/#23, in order: they must stay the first columns of results.csv
    ["id", "lang", "tier", "generated", "error", "provider_used", "attempts", "retries", "fallback_used", "bars", "midi_sha8"]
    + [c for m in ["tempo_match", "key_match", "time_signature_match", "bars_match", "bars_min", "instruments_included", "midi_instruments_included",
                   "scale_deviation", "pitch_range", "note_integrity", "no_empty_tracks", "midicheck_valid", "deterministic"] for c in (m, m + "_reason")]
    + ["mood_listen", "musicality_listen"]
)


EXPR_NAMES = ["melody_repetition", "melody_variation", "step_ratio", "leap_ratio", "contour_diversity", "final_is_tonic",
              "section_density_gap", "section_register_gap", "chord_voice_distance", "off_scale_rate"]
V02_COLUMNS = V01_COLUMNS + ["provider", "labels", "tempo_match_value", "scale_deviation_value", "pitch_range_value",
                             "bars_match_value", "bars_min_value", "axis1_ok", "곡ID", "언어"]


def _meta(bpm):
    return [(0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))), (0, mido.MetaMessage("time_signature", numerator=4, denominator=4))]


def _tempo_ctx(bpm, label, tol=None):
    ctx = _ctx(_midi([_meta(bpm), _notes(0, 0, [60])]), {"tempo_bpm": label})
    ctx.tempo_tol = tol
    return ctx


def test_same_midi_passes_or_fails_depending_on_tempo_tol():
    assert M.tempo_match(_tempo_ctx(105, 100)).status == "fail"                    # default: +-0.5 BPM
    r = M.tempo_match(_tempo_ctx(105, 100, tol=0.10))
    assert r.status == "pass" and "±10%" in r.reason and r.value == 105.0
    assert M.tempo_match(_tempo_ctx(111, 100, tol=0.10)).status == "fail"          # outside +-10% even with the option
    assert M.tempo_match(_tempo_ctx(110, 100, tol=0.10)).status == "pass"          # the border is inside (<=)
    assert M.tempo_match(_tempo_ctx(100.4, 100)).status == "pass"                  # default still tolerates 0.5 BPM
    assert M.tempo_match(_tempo_ctx(105, 100, tol=0.10)) == M.tempo_match(_tempo_ctx(105, 100, tol=0.10))


def test_tempo_tol_option_end_to_end_and_recorded_in_summary(tmp_path):
    data = copy.deepcopy(SET5)
    data["prompts"] = [{"id": "t1", "lang": "en", "tier": "A", "text": "x", "expect": {"tempo_bpm": 105}}]   # fake provider plays 100 BPM
    s = write(tmp_path, data)
    assert cli.main([str(s), "--provider", "fake", "--out", str(tmp_path / "d")]) == 0
    assert cli.main([str(s), "--provider", "fake", "--tempo-tol", "0.1", "--out", str(tmp_path / "t")]) == 0
    assert cli.main([str(s), "--provider", "fake", "--tempo-tol", "0.1", "--out", str(tmp_path / "t2")]) == 0
    import csv
    row = lambda d: list(csv.DictReader(open(tmp_path / d / "results.csv", encoding="utf-8")))[0]
    assert row("d")["tempo_match"] == "fail" and row("t")["tempo_match"] == "pass"
    assert row("d")["tempo_match_value"] == row("t")["tempo_match_value"] == "100.0"
    md_d, md_t = (tmp_path / "d" / "summary.md").read_text(encoding="utf-8"), (tmp_path / "t" / "summary.md").read_text(encoding="utf-8")
    assert "템포 ±0.5 BPM (기본)" in md_d and "템포 ±10.0% (상대, `--tempo-tol 0.1`)" in md_t
    for f in ("results.csv", "summary.md"):   # determinism holds with the option, too
        assert (tmp_path / "t" / f).read_bytes() == (tmp_path / "t2" / f).read_bytes()


@pytest.mark.parametrize("bad", ["0", "-0.1", "1.5", "abc"])
def test_tempo_tol_rejects_nonsense(tmp_path, bad):
    with pytest.raises(SystemExit) as e:
        cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--tempo-tol", bad, "--out", str(tmp_path / "o")])
    assert e.value.code == 2 and not (tmp_path / "o").exists()


def test_csv_keeps_the_old_columns_first_and_appends_the_new_ones(tmp_path):
    cols = report.columns()
    assert cols[:len(V01_COLUMNS)] == V01_COLUMNS
    assert cols[:len(V02_COLUMNS)] == V02_COLUMNS                      # v0.2 (PR #25) order is kept as well
    assert cols[len(V02_COLUMNS):len(V02_COLUMNS) + 2] == ["no_empty_tracks_value", "midi_instruments_included_value"]   # v0.3 additions
    assert cols[len(V02_COLUMNS) + 2:] == ["expr_" + n for n in EXPR_NAMES]                                          # v0.5 additions go last
    assert not any("시각" in c or "time" in c.lower().replace("time_signature", "") for c in cols)   # still no measurement time
    out = tmp_path / "out"
    cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)])
    import csv
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    assert list(rows["p1"]) == cols
    assert rows["p1"]["provider"] == "fake" and rows["p1"]["labels"] == "tonic;mode;tempo_bpm;time_signature;bars;instruments"
    assert rows["p3"]["labels"] == "" and rows["p4"]["labels"] == "tonic"
    assert rows["p1"]["tempo_match_value"] == "100.0" and rows["p3"]["tempo_match_value"] == ""          # na -> empty
    assert rows["p1"]["bars_match_value"] == "12" and rows["p3"]["bars_min_value"] == "12" and rows["p1"]["bars_min_value"] == ""
    assert float(rows["p2"]["scale_deviation_value"]) > 0.10 and rows["p1"]["scale_deviation_value"] == "0.0"
    assert float(rows["p1"]["pitch_range_value"]) <= 0.05
    assert all(r["곡ID"] == r["id"] and r["언어"] == r["lang"] for r in rows.values())
    assert all(r["axis1_ok"] == "True" for r in rows.values())


def test_axis1_failures_are_dropped_from_the_axis23_aggregate(tmp_path, monkeypatch):
    spec = copy.deepcopy(runner.FAKE_SPEC)
    spec["sections"] = [{"name": "A", "bars": 4, "chords": ["C", "G"], "energy": 0.5}]   # 4 bars: breaks the >=8 bar rule
    monkeypatch.setattr(runner, "FAKE_SPEC", spec)
    data = {"version": "1", "name": "axis1", "prompts": [
        {"id": "a", "lang": "en", "tier": "A", "text": "no bar count", "expect": {"tempo_bpm": 100}},   # 4 bars, rule applies -> axis 1 fail
        {"id": "b", "lang": "en", "tier": "A", "text": "four bars", "expect": {"tempo_bpm": 120, "bars": 4}},  # rule is na -> axis 1 ok, tempo fails
    ]}
    out = tmp_path / "out"
    assert cli.main([str(write(tmp_path, data)), "--provider", "fake", "--out", str(out)]) == 0
    import csv
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    assert rows["a"]["axis1_ok"] == "False" and rows["b"]["axis1_ok"] == "True"
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "축 1 통과(`axis1_ok`) 1/2" in md and "생성 성공 2/2" in md
    main_t = next(l for l in md.splitlines() if l.startswith("| 템포 일치"))
    axis_t = [l for l in md.splitlines() if l.startswith("| 템포 일치")][1]
    assert main_t.endswith("| 1/2 (50%) |"), main_t        # all pieces: a passes, b fails
    assert axis_t.endswith("| 0/1 (0%) |"), axis_t          # axis 1 survivors only: just b, which fails
    assert "| 축 1 통과 곡 수 / 전체 |" in md and md.count("## 축 1 통과 곡만의 집계") == 1


def test_axis1_is_false_when_generation_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "make_provider", lambda name: FakeProvider([ProviderError("boom")], name="fake", repeat_last=True))
    r = run5(tmp_path, check_determinism=False)
    assert report.axis1_ok(r["p1"]) is False and report.rows(list(r.values()))[0]["axis1_ok"] is False


def test_summary_header_and_label_count(tmp_path):
    out = tmp_path / "out"
    cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)])
    md = (out / "summary.md").read_text(encoding="utf-8")
    head = md.split("## 지표별")[0]
    assert f"`evalkit_version` {EVALKIT_VERSION}" in head and "허용오차: 템포 ±0.5 BPM (기본) · 마디 수 정확 일치" in head
    assert "유효 곡 비율: 생성 성공 5/5 · 축 1 통과(`axis1_ok`) 5/5" in head
    assert "tonic·mode 라벨이 **둘 다 있는 항목만** 채점한다: 이 세트에서 2/5개" in md     # p1, p2 (p4 has only the tonic)
    import re
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}", md)            # still no dates or times in the file


# ---------- 연구 1팀 2차 회신: key_match 는 사양 추출 일치로 분리, 1-8 은 축 1 에서 제외 ----------

def _summary(tmp_path, data=SET5, *args):
    out = tmp_path / "out"
    assert cli.main([str(write(tmp_path, data)), "--provider", "fake", "--out", str(out), *args]) == 0
    return (out / "summary.md").read_text(encoding="utf-8")


def _section(md, title):
    return md.split(f"## {title}")[1].split("\n## ")[0]


def test_key_match_is_a_spec_extraction_metric_not_an_axis23_metric(tmp_path):
    md = _summary(tmp_path)
    axis, extraction, pieces = _section(md, "축 1 통과 곡만의 집계 (축 2·3 지표)"), _section(md, "사양 추출 일치 (라벨 ↔ 사양 선언)"), _section(md, "곡(MIDI) 지표별 통과 비율")
    for name in ("key_match", "bars_match", "instruments_included"):
        assert f"`{name}`" in extraction and f"`{name}`" not in axis and f"`{name}`" not in pieces
    assert "| 조성 선언 일치(사양 추출) (`key_match`) |" in extraction
    assert "조성 일치" not in md.replace("조성 선언 일치", "")                    # the old name is gone from the summary
    assert "연구 1팀 2-2(사양 조 ↔ 곡에서 추정한 조)와 **다른 지표**" in extraction
    # the piece-level metrics stay where they were
    for name in ("tempo_match", "time_signature_match", "midi_instruments_included", "scale_deviation"):
        assert f"`{name}`" in axis and f"`{name}`" in pieces
    # values: p1 key labels match the fake spec, p2 do not; p4 (tonic only) is judged on the tonic; p3/p5 have no key label
    row = next(l for l in extraction.splitlines() if "`key_match`" in l)
    assert row.endswith("| 2/3 (67%) · na 2 |"), row
    assert md.index("## 사양 추출 일치") < md.index("## 축 1 통과 곡만의 집계")


def test_csv_keeps_the_key_match_column_name(tmp_path):
    out = tmp_path / "out"
    cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)])
    import csv
    rows = list(csv.DictReader(open(out / "results.csv", encoding="utf-8")))
    assert "key_match" in rows[0] and "key_match_reason" in rows[0]
    assert [r["key_match"] for r in rows] == ["pass", "fail", "na", "pass", "na"]


def test_one_eight_split_empty_tracks_in_axis1_instruments_in_axis3():
    from prompt2midi.evalkit.metrics import AXIS1_METRICS, AXIS23_METRICS, EXTRACTION_METRICS, Result
    assert "no_empty_tracks" in AXIS1_METRICS and "midi_instruments_included" not in AXIS1_METRICS   # 연구 1팀 3차 회신
    assert "instruments_included" not in AXIS1_METRICS and "instruments_included" not in AXIS23_METRICS   # spec-based: extraction
    assert set(AXIS1_METRICS) == {"midicheck_valid", "pitch_range", "note_integrity", "no_empty_tracks", "bars_min"}
    assert not set(EXTRACTION_METRICS) & set(AXIS23_METRICS)                       # no metric is counted in both groups
    results = {m.name: Result("pass") for m in METRICS}
    r = runner.ItemResult("x", "en", "A", True, "fake", [], "", "fake", 1, False, 12, "abcd1234", dict(results))
    r.results["no_empty_tracks"] = Result("fail", "빈 트랙 1개", 1)
    r.results["midi_instruments_included"] = Result("fail", "빠짐", 0.5)
    assert report.axis1_ok(r) is False                                             # an empty track is a file defect: axis 1
    r.results["no_empty_tracks"] = Result("pass", "", 0)
    assert report.axis1_ok(r) is True                                              # instrument failure alone does not drop the piece
    r.results["bars_min"] = Result("fail")
    assert report.axis1_ok(r) is False


def test_one_eight_value_columns(tmp_path):
    out = tmp_path / "out"
    cli.main([str(write(tmp_path, SET5)), "--provider", "fake", "--out", str(out)])
    import csv
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    assert all(r["no_empty_tracks_value"] == "0" for r in rows.values())
    assert rows["p1"]["midi_instruments_included_value"] == "1.0"                  # piano + drums both in the MIDI
    assert rows["p2"]["midi_instruments_included_value"] == "0.0"                  # violin is not
    assert rows["p3"]["midi_instruments_included_value"] == ""                     # no label


def test_new_summary_is_deterministic_and_has_the_new_version(tmp_path):
    for d in ("a", "b"):
        cli.main([str(write(tmp_path, SET5)), "--provider", "rule", "--tempo-tol", "0.1", "--out", str(tmp_path / d)])
    for f in ("results.csv", "summary.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()
    assert f"`evalkit_version` {EVALKIT_VERSION}" in (tmp_path / "a" / "summary.md").read_text(encoding="utf-8") and EVALKIT_VERSION == "0.5.0"


# ---------- v0.4: genre label (jazz/blues relax the scale-deviation limit to 25%) ----------

def _deviating_midi(off_notes, total=100):
    """`total` notes in C major (C D E G, repeated), of which the first `off_notes` are C# (outside C major)."""
    in_scale = [60, 62, 64, 67]
    pitches = [61 if i < off_notes else in_scale[i % 4] for i in range(total)]
    return _midi([META, _notes(0, 0, pitches)])


def _dev(off, genre=None, total=100):
    exp = {"tonic": "C", "mode": "major"}
    if genre is not None:
        exp["genre"] = genre
    return M.scale_deviation(_ctx(_deviating_midi(off, total), exp))


def test_same_13_percent_deviation_depends_on_the_genre_label():
    assert _dev(13).status == "fail"                                   # no genre label: limit 10% (unchanged behaviour)
    assert _dev(13, ["pop"]).status == "fail"                          # a genre that is not jazz/blues: still 10%
    for g in ("jazz", "blues"):
        r = _dev(13, [g])
        assert r.status == "pass" and "(재즈·블루스 완화 25%)" in r.reason and "≤25%" in r.reason and r.value == 0.13
    assert "완화" not in _dev(13).reason and "≤10%" in _dev(13).reason
    assert _dev(13, ["pop", "jazz"]).status == "pass"                  # jazz anywhere in the list relaxes it


def test_relaxed_limit_boundaries():
    assert _dev(10).status == "pass" and _dev(11).status == "fail"                        # default 10% (<=)
    assert _dev(25, ["blues"]).status == "pass" and _dev(26, ["blues"]).status == "fail"  # relaxed 25% (<=)
    assert _dev(13, ["jazz"], total=100).value == _dev(13, None, total=100).value          # the measured number does not change


def test_genre_label_is_used_by_no_other_metric(tmp_path):
    def run(genre):
        d = copy.deepcopy(SET5)
        for p in d["prompts"]:
            p["expect"] = dict(p.get("expect") or {}, **({"genre": genre} if genre else {}))
        r = run5(tmp_path, d)
        return {i: {m: (x.status, x.reason, x.value) for m, x in r[i].results.items() if m != "scale_deviation"} for i in r}
    assert run(["jazz"]) == run(None)


def test_genre_in_labels_column_summary_header_and_schema(tmp_path):
    d = copy.deepcopy(SET5)
    d["prompts"][0]["expect"]["genre"] = ["jazz", "pop"]
    out = tmp_path / "out"
    assert cli.main([str(write(tmp_path, d)), "--provider", "fake", "--out", str(out)]) == 0
    import csv
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    assert rows["p1"]["labels"] == "tonic;mode;tempo_bpm;time_signature;bars;instruments;genre" and "genre" not in rows["p2"]["labels"]
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "음계 이탈 ≤10% (genre 라벨에 jazz·blues 가 있으면 ≤25%)" in md and "`evalkit_version` 0.5.0" in md
    assert list(rows["p1"]) == report.columns() and report.columns()[:len(V02_COLUMNS)] == V02_COLUMNS and report.columns()[len(V02_COLUMNS):len(V02_COLUMNS) + 2] == ["no_empty_tracks_value", "midi_instruments_included_value"]
    props = model.json_schema()["$defs"]["Expect"]["properties"]
    assert "genre" in props and "genre" not in model.json_schema()["$defs"]["Expect"].get("required", [])
    assert set(model.json_schema()["$defs"]["Genre"]["enum"]) == {"pop", "rock", "jazz", "blues", "ballad", "bossa_nova", "electronic", "ambient", "classical", "lofi"}


def test_old_sets_without_genre_stay_valid_and_bad_genre_is_rejected(tmp_path):
    evalkit.load_set(write(tmp_path, SET5))                            # a v0.1-v0.3 style set: no genre anywhere
    for bad in (["metal"], [], "jazz", ["jazz", "jazz"], ["pop", "rock", "jazz", "blues"]):
        d = copy.deepcopy(SET5)
        d["prompts"][0]["expect"]["genre"] = bad
        with pytest.raises(evalkit.SetError):
            evalkit.load_set(write(tmp_path, d))
    # the same output as before for a set without genre labels: results are identical whether or not the field exists
    assert run5(tmp_path)["p1"].results["scale_deviation"].status == "pass"


def test_results_are_deterministic_with_genre_labels(tmp_path):
    d = copy.deepcopy(SET5)
    d["prompts"][1]["expect"]["genre"] = ["blues"]
    for n in ("a", "b"):
        cli.main([str(write(tmp_path, d)), "--provider", "rule", "--out", str(tmp_path / n)])
    for f in ("results.csv", "summary.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()
