"""proxy_details.jsonl: per-pair (M5) and per-slot (M6) records; the CSV keeps the neighbouring-pair maximum / the mean."""
import csv
import json
from pathlib import Path

import pytest

from prompt2midi.evalkit import __main__ as cli
from prompt2midi.evalkit import proxy as P
from prompt2midi.evalkit import report
from prompt2midi.evalkit.version import EVALKIT_VERSION

from tests.test_proxy import BAR, midi, prog, section_notes, spec_with, three

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples" / "baseline-v0" / "set.json"


def m5_result():
    notes = section_notes(0, [60, 62], 4) + section_notes(1, [67, 69], 8) + section_notes(2, [60, 62], 4)
    return P.evaluate(midi([notes]), spec_with(three()), None)["m5"]


def test_m5_details_hold_every_pair_with_its_kind_and_the_four_values():
    r = m5_result()
    secs = r.details["sections"]
    assert [(s["name"], s["start_bar"], s["end_bar"]) for s in secs] == [("A", 0, 4), ("B", 4, 8), ("A2", 8, 12)]
    assert [(s["density"], s["median_pitch"]) for s in secs] == [(4.0, 60), (8.0, 67), (4.0, 60)]
    pairs = r.details["pairs"]
    assert [(p["kind"], p["a"], p["b"]) for p in pairs] == [("adjacent", "A", "B"), ("adjacent", "B", "A2"), ("return", "A", "A2")]
    assert [(p["d_dens"], p["d_reg"], p["d_range"], p["d_vel"]) for p in pairs] == [(0.5, 7.0, 0.0, 0.0), (0.5, 7.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)]
    assert pairs[0]["contrast"] is True and pairs[1]["contrast"] is True and pairs[2]["close"] is True
    assert "close" not in pairs[0] and "contrast" not in pairs[2]


def test_the_csv_value_is_the_maximum_over_the_neighbouring_pairs_only():
    """A far-away return pair must not leak into the CSV maximum: only `adjacent` records count."""
    notes = section_notes(0, [60, 62], 4) + section_notes(1, [60, 62], 6) + section_notes(2, [60, 62], 8)
    r = P.evaluate(midi([notes]), spec_with(three(("A", "B", "C"))), None)["m5"]
    adj = [p for p in r.details["pairs"] if p["kind"] == "adjacent"]
    for k in ("d_dens", "d_reg", "d_range", "d_vel"):
        assert r.values[k] == max(p[k] for p in adj)
    far = section_notes(0, [60, 62], 4) + section_notes(1, [60, 62], 4) + section_notes(2, [80, 82], 16)
    r = P.evaluate(midi([far]), spec_with(three()), None)["m5"]                       # A -> A2 is a (far) return pair
    pairs = {(p["kind"], p["a"], p["b"]): p for p in r.details["pairs"]}
    assert pairs[("return", "A", "A2")]["d_reg"] == 20.0 and pairs[("return", "A", "A2")]["close"] is False
    assert r.values["d_reg"] == pairs[("adjacent", "B", "A2")]["d_reg"] == 20.0         # equal here because B->A2 is also 20 ...
    assert r.values["d_dens"] == max(p["d_dens"] for k, p in pairs.items() if k[0] == "adjacent")


def test_m6_details_have_one_record_per_slot_move():
    spec = spec_with([{"name": "A", "bars": 4, "chords": ["C", "F", "C", "F"], "energy": 0.5}], roles=("melody", "chords"))
    chords = ([(b * BAR, 2, p, BAR) for b, tri in enumerate(([60, 64, 67], [60, 65, 69], [60, 64, 67], [60, 65, 69])) for p in tri])
    r = P.evaluate(midi([[(0, 0, 60, 480)], chords]), spec, None)["m6"]
    mv = r.details["moves"]
    assert [(x["slot"], x["from"], x["to"], x["start_tick"]) for x in mv] == [(0, "C", "F", 0), (1, "F", "C", BAR), (2, "C", "F", 2 * BAR)]
    assert all(x["move_pc"] == 1.0 and x["changed"] is True and x["voicing_move"] == 1.0 for x in mv)
    assert r.values["chord_move_pc"] == sum(x["move_pc"] for x in mv) / len(mv)        # the CSV value is the mean of the records


def test_m6_a_repeated_chord_is_recorded_as_unchanged_and_na_has_no_details():
    r = P.evaluate(midi([[(0, 0, 60, 480)]]), prog(["C", "C", "G", "G"]), None)["m6"]
    assert [x["changed"] for x in r.details["moves"]] == [False, True, False] and r.details["moves"][0]["move_pc"] == 0.0
    assert r.details["moves"][0]["voicing_move"] is None                                # no chord track
    na = P.evaluate(midi([[(0, 0, 60, 480)]]), None, None)
    assert na["m5"].details == {} and na["m6"].details == {}


def run_cli(tmp_path, name, *extra):
    out = tmp_path / name
    assert cli.main([str(SAMPLES), "--provider", "rule", "--out", str(out), *extra]) == 0
    return out


def test_the_details_file_is_written_valid_and_byte_identical_across_runs(tmp_path):
    a, b = run_cli(tmp_path, "a"), run_cli(tmp_path, "b")
    for f in ("proxy_details.jsonl", "results.csv", "summary.md"):
        assert (a / f).read_bytes() == (b / f).read_bytes(), f
    lines = (a / "proxy_details.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(l) for l in lines]
    assert len(recs) == 16 and [r["id"] for r in recs][:2] == ["r01", "r02"]
    assert all({"id", "lang", "tier", "provider", "m5", "m6"} <= set(r) for r in recs) and not any("time" in k for k in recs[0])
    assert all(r["m5"]["status"] in ("in_range", "out_of_range", "na") for r in recs)


def test_csv_maximum_equals_the_details_for_all_baseline_samples(tmp_path):
    out = run_cli(tmp_path, "a")
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    recs = {r["id"]: r for r in (json.loads(l) for l in (out / "proxy_details.jsonl").read_text(encoding="utf-8").splitlines())}
    checked = 0
    for i, rec in recs.items():
        d = rec["m5"]["details"]
        if not d:
            continue
        adj = [p for p in d["pairs"] if p["kind"] == "adjacent"]
        for k in ("d_dens", "d_reg", "d_range", "d_vel"):
            assert float(rows[i][f"proxy_m5_{k}"]) == max(p[k] for p in adj), (i, k)
        ret = [p for p in d["pairs"] if p["kind"] == "return"]
        assert ret and rows[i]["proxy_m5_return_close"] == ("1.0" if all(p["close"] for p in ret) else "0.0"), i
        mv = rec["m6"]["details"]["moves"]
        assert float(rows[i]["proxy_m6_chord_move_pc"]) == pytest.approx(sum(m["move_pc"] for m in mv) / len(mv), abs=1e-3), i
        checked += 1
    assert checked == 16


def test_csv_columns_and_order_are_unchanged_by_the_details_file(tmp_path):
    out = run_cli(tmp_path, "a")
    header = (out / "results.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == report.columns() and header[-len(P.column_names()):] == P.column_names()
    assert not any("detail" in c for c in header) and len(P.column_names()) == 43      # same 43 proxy columns as v0.6
    assert EVALKIT_VERSION == "0.7.0"
    assert "proxy_details.jsonl" in (out / "summary.md").read_text(encoding="utf-8")
    assert "인접 섹션 쌍 중 최댓값" in (out / "summary.md").read_text(encoding="utf-8")


def test_docstrings_and_docs_state_the_reporting_rule():
    assert "LARGEST over the neighbouring section pairs" in P.m5_section_contrast.__doc__ and "proxy_details.jsonl" in P.m5_section_contrast.__doc__
    assert "MEANS" in P.m6_chord_movement.__doc__ and "proxy_details.jsonl" in P.m6_chord_movement.__doc__
    doc = (ROOT / "docs" / "eval-metrics.md").read_text(encoding="utf-8")
    assert "CSV 는 인접 쌍 최댓값" in doc and "proxy_details.jsonl" in doc and "5-2." in doc
