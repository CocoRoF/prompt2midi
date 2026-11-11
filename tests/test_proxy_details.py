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
    cols = P.column_names()
    assert not any("detail" in c for c in header) and len(cols) == 48
    assert cols[42] == "proxy_m7_off_dur_all_tracks" and cols[:3] == ["proxy_m1", "proxy_m1_reason", "proxy_m1_rep_rate"]   # the 43 columns of v0.6 are untouched
    assert cols[43:] == ["proxy_m5_ret_d_dens", "proxy_m5_ret_d_reg", "proxy_m5_ret_d_range", "proxy_m5_ret_d_vel", "proxy_m6_voicing_track"]   # v0.8 additions are last
    assert EVALKIT_VERSION == "0.8.0"
    assert "proxy_details.jsonl" in (out / "summary.md").read_text(encoding="utf-8")
    assert "인접 섹션 쌍 중 최댓값" in (out / "summary.md").read_text(encoding="utf-8")


def test_docstrings_and_docs_state_the_reporting_rule():
    assert "LARGEST over the neighbouring section pairs" in P.m5_section_contrast.__doc__ and "proxy_details.jsonl" in P.m5_section_contrast.__doc__
    assert "MEANS" in P.m6_chord_movement.__doc__ and "proxy_details.jsonl" in P.m6_chord_movement.__doc__
    doc = (ROOT / "docs" / "eval-metrics.md").read_text(encoding="utf-8")
    assert "CSV 는 인접 쌍 최댓값" in doc and "proxy_details.jsonl" in doc and "5-2." in doc


# ================================================== v0.8: answers of the 연구 1팀 to the 정의서 questions ==================================================

from prompt2midi.evalkit import model as eval_model



# ---- (A) return pairs: the confirmed rule ----

def test_return_pairs_confirmed_rule():
    rp = P.return_pairs
    assert rp(["A", "B", "A", "B2"]) == [(0, 2), (1, 3)]            # two pairs: (A,A) and (B,B2)
    assert rp(["A", "B", "A"]) == [(0, 2)]                          # the same name again is a return
    assert rp(["A", "A2", "A22"]) == [(0, 1), (1, 2)]               # A22 returns to A2 (A2 + "2"), not to A
    assert rp(["A", "A", "A"]) == [(0, 1), (0, 2), (1, 2)]          # duplicate names pair with every earlier equal name
    assert rp(["A", "B", "A2"]) == [(0, 2)] and rp(["A", "B", "C"]) == [] and rp(["A"]) == [] and rp([]) == []
    assert rp(["X2", "A"]) == []                                    # a "2" name without its base earlier is no return
    assert rp(["A2", "A"]) == []                                    # order matters: the later name must be equal or base+"2"
    assert rp(["A", "A22"]) == []                                   # "22" is not "2"
    assert rp(["A", "A3"]) == []                                    # only "2", no other digit


def four(names):
    return [{"name": n, "bars": 2, "chords": ["C"], "energy": 0.5} for n in names]


def sec_notes(i, pitches, per_bar, vel=80, bars=2):
    return [((i * bars + b) * BAR + k * (BAR // per_bar), 0, pitches[k % len(pitches)], BAR // per_bar // 2, vel) for b in range(bars) for k in range(per_bar)]


def test_m5_details_follow_the_confirmed_pairs_for_a_b_a_b2():
    notes = sec_notes(0, [60, 62], 4) + sec_notes(1, [67, 69], 8) + sec_notes(2, [60, 62], 4) + sec_notes(3, [67, 69], 8)
    r = P.evaluate(midi([notes]), spec_with(four(["A", "B", "A", "B2"])), None)["m5"]
    ret = [(p["a"], p["b"]) for p in r.details["pairs"] if p["kind"] == "return"]
    assert ret == [("A", "A"), ("B", "B2")] and r.values["return_close"] == 1.0 and r.status == P.IN_RANGE


# ---- (B) return-pair values in their own columns ----

def test_return_values_are_separate_columns_and_return_close_uses_each_pairs_own_values():
    near = sec_notes(0, [60, 62], 4) + sec_notes(1, [67, 69], 8) + sec_notes(2, [60, 62], 4) + sec_notes(3, [67, 69], 8)
    r = P.evaluate(midi([near]), spec_with(four(["A", "B", "A", "B2"])), None)["m5"]
    assert [r.values[k] for k in ("ret_d_dens", "ret_d_reg", "ret_d_range", "ret_d_vel")] == [0.0, 0.0, 0.0, 0.0]
    assert r.values["d_dens"] == 0.5 and r.values["d_reg"] == 7.0                       # the neighbouring maximum is untouched
    far = sec_notes(0, [60, 62], 4) + sec_notes(1, [67, 69], 8) + sec_notes(2, [60, 62], 4) + sec_notes(3, [90, 92], 16)
    r = P.evaluate(midi([far]), spec_with(four(["A", "B", "A", "B2"])), None)["m5"]
    pairs = {(p["a"], p["b"]): p for p in r.details["pairs"] if p["kind"] == "return"}
    assert pairs[("A", "A")]["close"] is True and pairs[("B", "B2")]["close"] is False and r.values["return_close"] == 0.0
    assert r.values["ret_d_reg"] == pairs[("B", "B2")]["d_reg"] == 23.0                 # max over the RETURN pairs
    assert r.values["ret_d_dens"] == max(p["d_dens"] for p in pairs.values()) == 0.5
    adj = [p for p in r.details["pairs"] if p["kind"] == "adjacent"]
    assert r.values["d_reg"] == max(p["d_reg"] for p in adj) == 30.0                    # A -> B2 (60 -> 90) is neighbouring; the return maximum (23) stays separate


def test_no_return_pairs_leaves_the_return_columns_empty():
    notes = sec_notes(0, [60, 62], 4) + sec_notes(1, [67, 69], 8) + sec_notes(2, [72, 74], 8)
    r = P.evaluate(midi([notes]), spec_with(four(["A", "B", "C"])), None)["m5"]
    assert r.values["return_close"] is None and all(r.values[k] is None for k in ("ret_d_dens", "ret_d_reg", "ret_d_range", "ret_d_vel"))


# ---- (C) M4 section_end_stable: only melody notes inside the section ----

def test_m4_a_section_without_melody_notes_is_skipped_and_borrows_nothing():
    secs = [{"name": n, "bars": 1, "chords": ["C"], "energy": 0.5} for n in "ABCD"]
    spec = spec_with(secs)
    # A ends on E (a C-major triad tone), B on D (not), C has NO melody note, D (last) ends on C
    notes = [(0, 0, 60, 480), (480, 0, 64, 480),                     # A: ... E
             (BAR, 0, 67, 480), (BAR + 480, 0, 62, 480),            # B: ... D
             (3 * BAR, 0, 60, 960)]                                 # D: C
    r = P.evaluate(midi([notes]), spec, None)["m4"]
    assert r.values["section_end_stable"] == 0.5                    # A stable, B not, C skipped -> 1 of 2 (the old code gave 1/3 because C borrowed B's D)
    all_empty = P.evaluate(midi([[(0, 0, 60, 480), (3 * BAR, 0, 60, 960)]]), spec, None)["m4"]
    assert all_empty.values["section_end_stable"] == 1.0            # only A has a note (C): stable; B and C skipped


def test_m4_no_melody_in_any_non_final_section_gives_none():
    spec = spec_with([{"name": n, "bars": 1, "chords": ["C"], "energy": 0.5} for n in "AB"])
    r = P.evaluate(midi([[(BAR, 0, 60, 960)]]), spec, None)["m4"]     # only the last section has a melody note
    assert r.values["section_end_stable"] is None and r.values["final_arrived"] == 1.0


# ---- (D) which track the voicing came from ----

def test_voicing_track_is_recorded_chords_or_pad():
    secs = [{"name": "A", "bars": 4, "chords": ["C", "F", "C", "F"], "energy": 0.5}]
    voic = lambda ch: [(b * BAR, ch, p, BAR) for b, tri in enumerate(([60, 64, 67], [60, 65, 69], [60, 64, 67], [60, 65, 69])) for p in tri]
    r = P.evaluate(midi([[(0, 0, 60, 480)], voic(2)]), spec_with(secs, roles=("melody", "chords")), None)["m6"]
    assert r.values["voicing_track"] == "chords" and r.details["voicing_track"] == "chords" and "보이싱 트랙: chords" in r.reason and "pad 로 대체" not in r.reason
    r = P.evaluate(midi([[(0, 0, 60, 480)], voic(3)]), _spec_pad(secs), None)["m6"]
    assert r.values["voicing_track"] == "pad" and "pad 로 대체" in r.reason and r.values["voicing_move"] == 1.0
    none = P.evaluate(midi([[(0, 0, 60, 480)]]), spec_with(secs), None)["m6"]
    assert none.values["voicing_track"] is None and none.details["voicing_track"] is None and "보이싱" not in none.reason


def _spec_pad(secs):
    d = spec_with(secs).model_dump(mode="json", by_alias=True)
    d["tracks"] = [d["tracks"][0], {"role": "pad", "instrument": "synth_pad"}]
    from prompt2midi.spec import MusicSpec
    return MusicSpec.model_validate(d)


# ---- the CSV carries the new columns last ----

def test_new_csv_columns_are_filled_for_the_baseline_samples(tmp_path):
    out = run_cli(tmp_path, "a")
    rows = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    recs = {r["id"]: json.loads(l) for l in (out / "proxy_details.jsonl").read_text(encoding="utf-8").splitlines() for r in [json.loads(l)]}
    for i, rec in recs.items():
        d = rec["m5"]["details"]
        ret = [p for p in d["pairs"] if p["kind"] == "return"]
        assert (rows[i]["proxy_m5_ret_d_dens"] == "") == (not ret), i
        if ret:
            assert float(rows[i]["proxy_m5_ret_d_reg"]) == max(p["d_reg"] for p in ret), i
        assert rows[i]["proxy_m6_voicing_track"] in ("chords", "pad", ""), i
        assert rec["m6"]["details"]["voicing_track"] in ("chords", "pad", None)


# ---- 연구 1팀 정의서 14절 hand-calculated specs (CI): hand-made notes, NOT text2midi output ----
# Same input layout as the research team's test_proxy.py (`_sec_notes`): per section (name, bars, melody 16th positions, melody pitches,
# bass 16th positions); melody notes are 2 sixteenths long (channel 0), bass notes 4 sixteenths long on pitch 40 (channel 1), velocity 80.

S16 = BAR // 16


def sec_notes_14(bars_spec):
    mel, bass, b = [], [], 0
    for name, nb, mpos, mp, bpos in bars_spec:
        for k in range(nb):
            mel += [((b + k) * BAR + pos * S16, 0, p, 2 * S16) for pos, p in zip(mpos, mp)]
            bass += [((b + k) * BAR + pos * S16, 1, 40, 4 * S16) for pos in bpos]
        b += nb
    return [mel, bass]


def named_spec(sections, roles=("melody", "bass")):
    return spec_with([{"name": n, "bars": nb, "chords": ["C"], "energy": 0.5} for n, nb, *_ in sections], roles=roles)


def test_hand_spec_m5_two_sections_contrast_and_return_close():
    # 사양 1: A4·B4·A2 4 (12 bars). density A = 4+2 = 6/bar, B = 8+2 = 10/bar, A2 = 6; median A 62, B 74; range (P90-P10) 4 everywhere; velocity 80.
    secs = [("A", 4, [0, 4, 8, 12], [60, 62, 64, 62], [0, 8]), ("B", 4, [0, 2, 4, 6, 8, 10, 12, 14], [72, 74, 76, 74, 72, 74, 76, 74], [0, 8]), ("A2", 4, [0, 4, 8, 12], [60, 62, 64, 62], [0, 8])]
    r = P.evaluate(midi(sec_notes_14(secs)), named_spec(secs), None)["m5"]
    adj = [p for p in r.details["pairs"] if p["kind"] == "adjacent"]
    ret = [p for p in r.details["pairs"] if p["kind"] == "return"]
    assert [(p["a"], p["b"]) for p in adj] == [("A", "B"), ("B", "A2")]
    assert all((p["d_dens"], p["d_reg"], p["d_range"], p["d_vel"]) == (0.4, 12, 0, 0.0) for p in adj)
    assert [(p["a"], p["b"]) for p in ret] == [("A", "A2")] and (ret[0]["d_dens"], ret[0]["d_reg"]) == (0.0, 0)
    assert (r.values["contrast_present"], r.values["return_close"], r.status) == (1.0, 1.0, P.IN_RANGE)
    assert (r.values["d_dens"], r.values["d_reg"], r.values["ret_d_dens"], r.values["ret_d_reg"]) == (0.4, 12.0, 0.0, 0.0)


def test_hand_spec_m5_max_of_adjacent_is_not_the_return_pair_value():
    # 사양 2: density A = 4+2 = 6, B = 6+2 = 8, A2 = 2+2 = 4. adjacent 0.25 and 0.5 (max 0.5); the return pair A-A2 is 2/6 = 0.3333 > 0.25 -> not close.
    secs = [("A", 4, [0, 4, 8, 12], [60] * 4, [0, 8]), ("B", 4, [0, 2, 6, 8, 10, 14], [60] * 6, [0, 8]), ("A2", 4, [0, 8], [60] * 2, [0, 8])]
    r = P.evaluate(midi(sec_notes_14(secs)), named_spec(secs), None)["m5"]
    adj = [p["d_dens"] for p in r.details["pairs"] if p["kind"] == "adjacent"]
    ret = [p for p in r.details["pairs"] if p["kind"] == "return"]
    assert adj == [0.25, 0.5] and r.values["d_dens"] == 0.5 == max(adj)
    assert ret[0]["d_dens"] == 0.3333 and ret[0]["close"] is False and r.values["ret_d_dens"] == 0.3333
    assert (r.values["contrast_present"], r.values["return_close"], r.status) == (1.0, 0.0, P.OUT_OF_RANGE)


def chord_spec(sections):
    return spec_with([{"name": n, "bars": len(ch), "chords": ch, "energy": 0.5} for n, ch in sections], roles=("melody", "chords"))


def test_hand_spec_m6_slots_across_the_section_boundary():
    notes = [(0, 0, 60, 480)]
    # A=[C,G], B=[Am,F]: C->G 1.0, G->Am 1.6667 (across the boundary), Am->F 0.3333 -> mean 1.0, change rate 1.0
    r = P.evaluate(midi([notes]), chord_spec([("A", ["C", "G"]), ("B", ["Am", "F"])]), None)["m6"]
    assert (r.values["chord_move_pc"], r.values["chord_change_rate"], r.status) == (1.0, 1.0, P.IN_RANGE)
    assert [m["move_pc"] for m in r.details["moves"]] == [1.0, 1.6667, 0.3333]
    # same names in a row: C,C,Am,Am -> 0, 0.6667, 0 -> mean 0.2222, change rate 1/3 -> out of range (mean < 0.5)
    r = P.evaluate(midi([notes]), chord_spec([("A", ["C", "C"]), ("B", ["Am", "Am"])]), None)["m6"]
    assert (r.values["chord_move_pc"], r.values["chord_change_rate"], r.status) == (0.2222, 0.3333, P.OUT_OF_RANGE)


def test_hand_spec_m6_voicing_c_f_g():
    # voicings C (60,64,67), F (60,65,69), G (59,62,67): C->F 1.0, F->G 2.0 -> mean 1.5; the chord track (role chords, channel 2) is recorded
    spec = chord_spec([("A", ["C", "F", "G"])])
    chords = [(b * BAR, 2, p, BAR) for b, tri in enumerate(([60, 64, 67], [60, 65, 69], [59, 62, 67])) for p in tri]
    r = P.evaluate(midi([[(0, 0, 60, 480)], chords]), spec, None)["m6"]
    assert r.values["voicing_move"] == 1.5 and r.status == P.IN_RANGE and r.values["voicing_track"] == "chords"
    assert [m["voicing_move"] for m in r.details["moves"]] == [1.0, 2.0]


# ---- local manual check against the research team's text2midi pieces (NOT run in CI; no text2midi output is kept in this repository) ----

import os

RESEARCH1_DIR = os.environ.get("P2M_RESEARCH1_DIR")
TOL = 1e-3


def _close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) <= TOL)


@pytest.mark.skipif(not RESEARCH1_DIR, reason="local manual check: set P2M_RESEARCH1_DIR to the research team's folder (prompt01/05/10.mid, prompts.json, text2midi10_결과.json); "
                                              "text2midi output and values derived from it are not kept in the repository (legal review pending)")
@pytest.mark.parametrize("pid", [1, 5, 10])
def test_matches_the_research_reference_values_locally(pid):
    folder = Path(RESEARCH1_DIR)
    prompts = {p["id"]: p for p in json.loads((folder / "prompts.json").read_text(encoding="utf-8"))}
    ref = {r["id"]: r for r in json.loads((folder / "text2midi10_결과.json").read_text(encoding="utf-8"))}[pid]
    tonic, mode = prompts[pid]["key"].split(" ", 1)
    res = P.evaluate((folder / f"prompt{pid:02d}.mid").read_bytes(), None, eval_model.Expect.model_validate({"tonic": tonic, "mode": mode}))
    for ours, theirs, names in (("m1", "M1", ("rep_rate", "exact_rate", "var_rate", "new_rate")),
                                ("m2", "M2", ("step_share", "leap_share", "huge_share", "repeat_share", "skip_share", "recovery")),
                                ("m3", "M3", ("diversity", "adjacent_same")),
                                ("m4", "M4", ("final_is_tonic", "final_arrived", "final_score", "bass_final_tonic", "section_end_stable")),
                                ("m7", "M7", ("off_all", "off_strong", "off_dur_all_tracks"))):
        assert res[ours].status == ref[theirs]["status"], (pid, ours)
        for n in names:
            if n in ref[theirs] or res[ours].status != P.NA:
                assert _close(res[ours].values.get(n), ref[theirs].get(n)), (pid, ours, n)
