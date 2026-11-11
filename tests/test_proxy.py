"""M1..M7 proxy indicators (연구 1팀 정의서 `자동_대리지표_정의.md`): the test vectors of the definition, built by hand."""
import copy
import io

import mido
import pytest

from prompt2midi.evalkit import model
from prompt2midi.evalkit import proxy as P
from prompt2midi.evalkit.runner import FAKE_SPEC
from prompt2midi.spec import MusicSpec

TPQ, BAR = 480, 1920


def midi(tracks, num=4, den=4, extra_sigs=()):
    """tracks: list of [(start_tick, channel, pitch, length, velocity)] -> bytes (one MIDI track each, plus a meta track)."""
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    meta = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0),
                           mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0)])
    for tick, n, d in extra_sigs:
        meta.append(mido.MetaMessage("time_signature", numerator=n, denominator=d, time=tick))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)
    for notes in tracks:
        ev = []
        for note in notes:
            start, ch, p, ln = note[:4]
            vel = note[4] if len(note) > 4 else 80
            ev += [(start, 1, mido.Message("note_on", channel=ch, note=p, velocity=vel)), (start + ln, 0, mido.Message("note_off", channel=ch, note=p, velocity=0))]
        ev.sort(key=lambda e: (e[0], e[1]))
        tr, t = mido.MidiTrack(), 0
        for tick, _o, msg in ev:
            tr.append(msg.copy(time=tick - t))
            t = tick
        tr.append(mido.MetaMessage("end_of_track", time=0))
        mid.tracks.append(tr)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def mel(pitches, positions, bar, ln=240, ch=0, vel=80):
    """Notes of one bar: pitches at 16th-grid positions."""
    return [(bar * BAR + pos * 120, ch, p, ln, vel) for p, pos in zip(pitches, positions)]


def seq(pitches, step=480, start=0, ln=240, ch=0):
    return [(start + i * step, ch, p, ln) for i, p in enumerate(pitches)]


def spec_with(sections, key=("C", "major"), roles=("melody",)):
    d = copy.deepcopy(FAKE_SPEC)
    d["key"] = {"tonic": key[0], "mode": key[1]}
    d["sections"] = sections
    tracks = {"melody": d["tracks"][0], "bass": {"role": "bass", "instrument": "electric_bass"}, "chords": {"role": "chords", "instrument": "piano"}}
    d["tracks"] = [tracks[r] for r in roles]
    return MusicSpec.model_validate(d)


def run(key, tracks, spec=None, expect=None, valid=True, **kw):
    return P.evaluate(midi(tracks, **kw), spec, expect, valid)[key]


def label(tonic, mode):
    return model.Expect.model_validate({"tonic": tonic, "mode": mode})


# ================================================================================ common rules

def test_status_names_are_not_pass_fail_and_thresholds_live_in_one_table():
    res = P.evaluate(midi([seq([60, 62, 64, 65, 67, 69, 71, 72, 74])]), None, None)
    assert set(res) == {"m1", "m2", "m3", "m4", "m5", "m6", "m7"}
    assert all(r.status in (P.IN_RANGE, P.OUT_OF_RANGE, P.NA) for r in res.values())
    assert {P.IN_RANGE, P.OUT_OF_RANGE, P.NA} == {"in_range", "out_of_range", "na"}
    assert isinstance(P.THRESH, dict) and P.THRESH["m7_off_all_max"] == 0.10 and P.THRESH["m2_step_range"] == (0.35, 0.80)


def test_docstrings_name_the_proxied_property_the_limits_and_the_temporary_thresholds():
    assert "TEMPORARY" in P.__doc__ and "quality" in P.__doc__ and "THRESH" in P.__doc__
    for key, _t, fn, _n in P.PROXIES:
        doc = fn.__doc__ or ""
        assert "Stands in for" in doc and "Limits" in doc and "TEMPORARY" in doc, key


def test_invalid_pieces_are_na_with_a_reason():
    res = P.evaluate(midi([seq([60, 62, 64, 65, 67, 69, 71, 72, 74])]), None, None, valid=False)
    assert all(r.status == P.NA and r.reason == "유효성 실패" and set(r.values.values()) == {None} for r in res.values())
    assert all(r.status == P.NA for r in P.evaluate(None).values())
    assert all(r.status == P.NA for r in P.evaluate(b"not midi").values())


def test_evaluation_is_deterministic():
    data = midi([seq([60, 62, 64, 65, 67, 69, 71, 72, 74, 72, 71, 69])])
    assert P.evaluate(data, None, label("C", "major")) == P.evaluate(data, None, label("C", "major"))


def test_drum_channel_notes_are_ignored_and_changing_meter_makes_bar_metrics_na():
    drums = [(i * 480, 9, 36, 100) for i in range(40)]
    a = P.evaluate(midi([seq([60, 62, 64, 65, 67, 69, 71, 72, 74])]), None, label("C", "major"))
    b = P.evaluate(midi([seq([60, 62, 64, 65, 67, 69, 71, 72, 74]), drums]), None, label("C", "major"))
    assert a == b
    bars = [n for b_ in range(4) for n in mel([60, 62, 64, 65], [0, 4, 8, 12], b_)]
    changing = midi([bars], extra_sigs=[(BAR * 2, 3, 4)])
    res = P.evaluate(changing, None, label("C", "major"))
    assert [res[k].status for k in ("m1", "m3", "m4", "m5")] == ["na"] * 4 and "박자표" in res["m1"].reason
    assert res["m7"].status != "na"                                     # M7 is not bar based


# ================================================================================ M1

R4 = [0, 4, 8, 12]


def test_m1_four_identical_bars_is_exact_and_out_of_range():
    tracks = [[n for b in range(4) for n in mel([60, 62, 64, 65], R4, b)]]
    r = run("m1", tracks)
    assert r.values == {"rep_rate": 1.0, "exact_rate": 1.0, "var_rate": 0.0, "new_rate": 0.0} and r.status == P.OUT_OF_RANGE


def test_m1_all_different_bars_are_new():
    bars = [mel([60, 62, 64, 65], R4, 0), mel([72, 60], [0, 2], 1), mel([70, 77], [6, 10], 2), mel([60, 69], [2, 14], 3)]
    r = run("m1", [[n for b in bars for n in b]])
    assert r.values["new_rate"] == 1.0 and r.values["rep_rate"] == 0.0 and r.status == P.OUT_OF_RANGE


def test_m1_a_changed_last_interval_counts_as_variation():
    a, v = [60, 62, 64, 65], [60, 62, 64, 67]                 # interval classes [1,1,1] vs [1,1,2]: s = 0.5*1 + 0.5*(1-1/3) = 0.8333
    r = run("m1", [[n for b, p in enumerate([a, v, a, v]) for n in mel(p, R4, b)]])
    assert r.values["var_rate"] == 0.3333 and r.values["exact_rate"] == 0.6667 and r.values["rep_rate"] == 0.6667 and r.values["new_rate"] == 0.0
    assert r.values["exact_rate"] + r.values["var_rate"] + r.values["new_rate"] == pytest.approx(1.0, abs=1e-3)   # rep includes exact


def test_m1_rates_add_up():
    a, b, c = [60, 62, 64, 65], [67, 65, 64, 62], [60, 62, 64, 67]
    bars = [a, b, a, b, c, b, a, c]
    r = run("m1", [[n for i, p in enumerate(bars) for n in mel(p, R4 if i % 2 == 0 else [0, 2, 8, 12], i)]])
    v = r.values
    assert v["exact_rate"] + v["var_rate"] + v["new_rate"] + (v["rep_rate"] - v["exact_rate"]) == pytest.approx(1.0, abs=1e-3)
    assert r.status in (P.IN_RANGE, P.OUT_OF_RANGE)


def test_m1_fewer_than_four_bars_is_na():
    r = run("m1", [[n for b in range(3) for n in mel([60, 62, 64, 65], R4, b)]])
    assert r.status == P.NA and set(r.values.values()) == {None}


# ================================================================================ M2

def test_m2_ascending_major_scale_is_all_steps():
    r = run("m2", [seq([60, 62, 64, 65, 67, 69, 71, 72, 74])])
    assert r.values["step_share"] == 1.0 and r.values["leap_share"] == 0.0 and r.values["recovery"] is None
    assert r.status == P.OUT_OF_RANGE                          # step_share 1.0 is above the 0.80 upper bound (temporary range)


def test_m2_seven_minus_one_minus_six_is_leapy_and_recovery_is_defined():
    r = run("m2", [seq([60, 67, 66, 60, 67, 66, 60, 67, 66, 60])])
    assert r.values["leap_share"] == 0.6667 > 0.5 and r.values["recovery"] == 0.6 and r.status == P.OUT_OF_RANGE


def test_m2_one_note_per_bar_leaves_no_pairs():
    r = run("m2", [[(i * BAR, 0, 60 + i % 5, 480) for i in range(10)]])
    assert r.status == P.NA


def test_m2_shares_and_in_range_case():
    # intervals: 1 3 2 0 1 2 3 5 1 2 = 10 pairs: step 1,2,1,2,1,2 ... counted below
    p = [60]
    for d in (1, 3, 2, 0, 1, 2, -3, 5, 1, 2):
        p.append(p[-1] + d)
    r = run("m2", [seq(p)])
    assert r.values["repeat_share"] == 0.1 and r.values["step_share"] == 0.6 and r.values["skip_share"] == 0.2 and r.values["leap_share"] == 0.1
    assert r.values["huge_share"] == 0.0 and r.values["recovery"] is None                    # fewer than 3 leaps
    assert r.status == P.IN_RANGE


def test_m2_a_rest_longer_than_two_beats_breaks_the_phrase():
    notes = seq([60, 62, 64, 65, 67], start=0) + seq([79, 81, 83, 84, 86], start=10 * 480)   # 67 -> 79 jumps an octave, but after a long rest
    r = run("m2", [notes])
    assert r.status != P.NA and r.values["huge_share"] == 0.0 and r.values["leap_share"] == 0.0   # the pair across the rest is not an interval of the phrase
    only_one_side = run("m2", [seq([60, 62, 64, 65, 67], start=0) + seq([79, 81, 83], start=10 * 480)])
    assert only_one_side.status == P.NA                                                        # 4 + 2 pairs < 8


# ================================================================================ M3

def window_notes(shape, w):
    """4 notes inside the 2-bar window `w` (bars 2w, 2w+1)."""
    return [(w * 2 * BAR + i * 480, 0, p, 240) for i, p in enumerate(shape)]


ARCH, ASC, DESC, VALLEY, FLAT = [60, 64, 67, 63], [60, 62, 64, 67], [67, 64, 62, 60], [64, 60, 62, 65], [60, 62, 61, 63]


def test_m3_contour_classes():
    assert [P.contour_class(p) for p in (ARCH, ASC, DESC, VALLEY, FLAT)] == ["arch", "ascending", "descending", "valley", "flat"]
    assert P.contour_class([60, 70, 60, 60, 60, 60, 61]) == "wave"          # peak at 1/6 of the window: not an arch; nothing else fits


def test_m3_four_shapes_twice_is_diverse():
    r = run("m3", [[n for w, s in enumerate([ARCH, ASC, DESC, VALLEY] * 2) for n in window_notes(s, w)]])
    assert r.values == {"diversity": 0.7737, "adjacent_same": 0.0} and r.status == P.IN_RANGE        # 2 / log2(6)


def test_m3_all_ascending_is_out_of_range():
    r = run("m3", [[n for w in range(8) for n in window_notes(ASC, w)]])
    assert r.values == {"diversity": 0.0, "adjacent_same": 1.0} and r.status == P.OUT_OF_RANGE


def test_m3_needs_four_windows_of_four_notes():
    assert run("m3", [[n for w in range(3) for n in window_notes(ASC, w)]]).status == P.NA
    thin = [(w * 2 * BAR + i * 480, 0, 60 + i, 240) for w in range(8) for i in range(3)]             # 3 notes per window
    assert run("m3", [thin]).status == P.NA


# ================================================================================ M4

def final(pitch, start, ln, bars=2):
    lead = [(0, 0, 60, 480), (480, 0, 62, 480)]
    return lead + [(start, 0, pitch, ln)]


C = label("C", "major")


def test_m4_long_tonic_in_the_last_bar_arrives():
    r = run("m4", [final(60, BAR + 480, 960)], expect=C)
    assert r.values["final_is_tonic"] == 1.0 and r.values["final_arrived"] == 1.0 and r.values["final_score"] == 1.0 and r.status == P.IN_RANGE


def test_m4_other_endings():
    d = run("m4", [final(62, BAR + 480, 960)], expect=C)
    assert (d.values["final_arrived"], d.values["final_score"]) == (0.0, 0.0) and d.status == P.OUT_OF_RANGE
    e = run("m4", [final(64, BAR + 480, 960)], expect=C)
    assert (e.values["final_arrived"], e.values["final_score"]) == (0.0, 0.5)
    short_weak = run("m4", [final(60, BAR + 480, 120)], expect=C)                     # a short C on a weak beat does not arrive
    assert short_weak.values["final_is_tonic"] == 1.0 and short_weak.values["final_arrived"] == 0.0
    downbeat = run("m4", [final(60, BAR, 120)], expect=C)                             # first beat of the bar counts even if short
    assert downbeat.values["final_arrived"] == 1.0
    not_last_bar = run("m4", [final(60, 480, 960) + [(2 * BAR - 240, 1, 40, 240)]], expect=C)
    assert not_last_bar.values["final_arrived"] == 0.0                                # the tonic sounds, but the piece goes on after it


def test_m4_without_a_key_is_na():
    assert run("m4", [final(60, BAR + 480, 960)]).status == P.NA
    assert run("m4", [final(60, BAR + 480, 960)], expect=model.Expect.model_validate({"tonic": "C"})).status == P.NA


def test_m4_bass_final_and_section_end_stable():
    sections = [{"name": "A", "bars": 1, "chords": ["C"], "energy": 0.5}, {"name": "B", "bars": 1, "chords": ["C"], "energy": 0.5}]
    spec = spec_with(sections, roles=("melody", "bass"))
    melody = [(0, 0, 64, 480), (480, 0, 67, 480), (BAR, 0, 60, 960)]            # section A ends on G (triad tone), the piece ends on C
    bass = [(0, 1, 36, 480), (BAR, 1, 36, 960)]
    r = run("m4", [melody, bass], spec=spec)
    assert r.values["bass_final_tonic"] == 1.0 and r.values["section_end_stable"] == 1.0 and r.status == P.IN_RANGE
    unstable = run("m4", [[(0, 0, 64, 480), (480, 0, 62, 480), (BAR, 0, 60, 960)], bass], spec=spec)     # A ends on D, not a triad tone
    assert unstable.values["section_end_stable"] == 0.0 and unstable.status == P.OUT_OF_RANGE


def test_m4_triad_tables_including_the_modes_of_our_spec():
    assert P._triad(0, "major") == {0, 4, 7} and P._triad(9, "minor") == {9, 0, 4} and P._triad(2, "dorian") == {2, 5, 9}
    assert P._triad(4, "phrygian") == {4, 7, 11} and P._triad(5, "lydian") == {5, 9, 0} and P._triad(7, "mixolydian") == {7, 11, 2}
    assert P._triad(9, "harmonic_minor") == {9, 0, 4} and P._triad(11, "locrian") == {11, 2, 5}


# ================================================================================ M5

def three(names=("A", "B", "A2"), chords=("C",)):
    return [{"name": n, "bars": 4, "chords": list(chords), "energy": 0.5} for n in names]


def section_notes(i, pitches, per_bar, vel=80):
    out = []
    for b in range(4):
        for k in range(per_bar):
            out.append(((i * 4 + b) * BAR + k * (BAR // per_bar), 0, pitches[k % len(pitches)], BAR // per_bar // 2, vel))
    return out


def test_m5_a_contrasting_middle_section_that_returns_is_in_range():
    notes = section_notes(0, [60, 62], 4) + section_notes(1, [67, 69], 8) + section_notes(2, [60, 62], 4)
    r = run("m5", [notes], spec=spec_with(three()))
    assert r.values["contrast_present"] == 1.0 and r.values["return_close"] == 1.0 and r.status == P.IN_RANGE
    assert r.values["d_dens"] == 0.5 and r.values["d_reg"] == 7.0


def test_m5_identical_sections_have_no_contrast():
    notes = section_notes(0, [60, 62], 4) + section_notes(1, [60, 62], 4) + section_notes(2, [60, 62], 4)
    r = run("m5", [notes], spec=spec_with(three()))
    assert r.values["contrast_present"] == 0.0 and r.status == P.OUT_OF_RANGE


def test_m5_loudness_alone_is_contrast_and_a_far_return_fails():
    quiet = section_notes(0, [60, 62], 4, vel=60) + section_notes(1, [60, 62], 4, vel=80) + section_notes(2, [60, 62], 4, vel=60)
    assert run("m5", [quiet], spec=spec_with(three())).values["contrast_present"] == 1.0           # d_vel = 20 >= 8
    far = section_notes(0, [60, 62], 4) + section_notes(1, [67, 69], 8) + section_notes(2, [72, 74], 8)
    r = run("m5", [far], spec=spec_with(three()))
    assert r.values["return_close"] == 0.0 and r.status == P.OUT_OF_RANGE


def test_m5_one_section_or_no_spec_is_na():
    assert run("m5", [section_notes(0, [60], 4)], spec=spec_with(three(("A",)))).status == P.NA
    assert run("m5", [section_notes(0, [60], 4)]).status == P.NA                                  # external MIDI: no sections, never split by guess


def test_m5_return_pairs_follow_the_expander_naming():
    assert P.return_pairs(["A", "B", "A2"]) == [(0, 2)] and P.return_pairs(["A", "B", "C"]) == [] and P.return_pairs(["X2", "A"]) == []


# ================================================================================ M6

def prog(names):
    return spec_with([{"name": "A", "bars": len(names), "chords": names, "energy": 0.5}])


def m6_mean(names):
    return run("m6", [seq([60, 62, 64, 65])], spec=prog(names))


def test_m6_distance_vectors():
    pcs = P.chord_pcs
    d = lambda a, b: round(P._move(list(pcs(a)), list(pcs(b)), P._circ), 4)  # noqa: E731
    assert d("C", "C") == 0 and d("C", "Am") == 0.6667 and d("C", "F") == 1.0 and d("C", "G") == 1.0
    assert (d("C", "G"), d("G", "Am"), d("Am", "F"), d("F", "C")) == (1.0, 1.6667, 0.3333, 1.0)
    assert pcs("Bbm7") == (1, 5, 8, 10) and pcs("C#dim") == (1, 4, 7)
    assert P._move([60, 64, 67], [60, 65, 69], lambda a, b: abs(a - b)) == 1.0                  # realised voicing C -> F


def test_m6_reference_progressions():
    assert m6_mean(["C", "G", "Am", "F"]).values["chord_move_pc"] == 1.0
    assert m6_mean(["Dm7", "G7", "Cmaj7", "Am7"]).values["chord_move_pc"] == 0.6667
    assert m6_mean(["C", "F", "G", "C"]).values["chord_move_pc"] == 1.3333
    assert m6_mean(["Am", "G", "F", "E"]).values["chord_move_pc"] == 1.5556
    assert m6_mean(["C", "G", "Am", "F"]).status == P.IN_RANGE


def test_m6_a_static_progression_is_out_of_range_and_short_ones_are_na():
    r = m6_mean(["C", "C", "C", "C"])
    assert r.values["chord_move_pc"] == 0.0 and r.values["chord_change_rate"] == 0.0 and r.status == P.OUT_OF_RANGE
    assert m6_mean(["C", "G"]).status == P.NA
    assert run("m6", [seq([60, 62])]).status == P.NA                                              # no spec: chords are never guessed from the MIDI


def test_m6_voicing_move_from_the_chord_track():
    spec = spec_with([{"name": "A", "bars": 4, "chords": ["C", "F", "C", "F"], "energy": 0.5}], roles=("melody", "chords"))
    chords = [(0, 2, p, BAR) for p in (60, 64, 67)] + [(BAR, 2, p, BAR) for p in (60, 65, 69)] + [(2 * BAR, 2, p, BAR) for p in (60, 64, 67)] + [(3 * BAR, 2, p, BAR) for p in (60, 65, 69)]
    r = run("m6", [seq([60, 62, 64, 65]), chords], spec=spec)
    assert r.values["voicing_move"] == 1.0 and r.values["chord_move_pc"] == 1.0 and r.status == P.IN_RANGE
    wide = [(0, 2, p, BAR) for p in (48, 52, 55)] + [(BAR, 2, p, BAR) for p in (65, 69, 72)] + [(2 * BAR, 2, p, BAR) for p in (48, 52, 55)] + [(3 * BAR, 2, p, BAR) for p in (65, 69, 72)]
    r = run("m6", [seq([60, 62, 64, 65]), wide], spec=spec)
    assert r.values["voicing_move"] > P.THRESH["m6_voicing_max"] and r.status == P.OUT_OF_RANGE


# ================================================================================ M7

def test_m7_c_major_melody_stays_in_the_scale():
    r = run("m7", [seq([60, 62, 64, 65, 67, 69, 71, 72])], expect=C)
    assert r.values["off_all"] == 0.0 and r.values["off_strong"] == 0.0 and r.status == P.IN_RANGE


def test_m7_one_foreign_first_note():
    pitches = [66] + [60, 62, 64, 65, 67, 69, 71, 72, 74, 72, 71, 69, 67, 65, 64]               # 16 notes, the first one F#
    r = run("m7", [seq(pitches)], expect=C)
    assert r.values["off_all"] == 0.0625 and r.values["off_strong"] == 0.0625 and r.status == P.OUT_OF_RANGE   # off_strong 1/16 > 0.05


def test_m7_duration_weighting_over_all_tracks_and_no_strong_notes():
    r = run("m7", [seq([60, 62, 64, 65], ln=480), [(0, 1, 61, 480)]], expect=C)                  # an off-scale bass note: 480 of 4*480+480 ticks
    assert r.values["off_dur_all_tracks"] == 0.2
    offbeat = run("m7", [[(120 + i * 480, 0, p, 100) for i, p in enumerate((60, 62, 64, 65))]], expect=C)         # none on a quarter position, none >= 1 beat
    assert offbeat.values["off_strong"] is None and offbeat.status == P.IN_RANGE


def test_m7_modes_of_our_spec_and_unknown_mode():
    a_harm = label("A", "harmonic_minor")
    assert run("m7", [seq([57, 59, 60, 62, 64, 65, 68, 69])], expect=a_harm).values["off_all"] == 0.0        # G# (68) belongs to A harmonic minor
    assert run("m7", [seq([57, 59, 60, 62, 64, 65, 68, 69])], expect=label("A", "minor")).values["off_all"] == 0.125
    assert P.MODE_SCALE["harmonic_minor"] == (0, 2, 3, 5, 7, 8, 11)
    mat = P.build_material(midi([seq([60, 62])]), None, C)
    mat.mode = "not_a_mode"
    assert P.m7_off_scale(mat).status == P.NA and P.m4_final_note(mat).status == P.NA
    assert run("m7", [seq([60, 62])]).status == P.NA                                              # key unknown, never estimated


def test_the_key_is_never_estimated_the_spec_wins_over_the_label():
    spec = spec_with(three(), key=("D", "major"))
    r = run("m7", [seq([61, 62, 64])], spec=spec, expect=label("C", "major"))
    assert r.values["off_all"] == 0.0                                                          # the spec's D major (C# in scale) is used, not the label
