"""Expressiveness proxy indicators: expected values computed by hand from tiny MIDI files."""
import io

import mido
import pytest

from prompt2midi.evalkit import expressiveness as E
from prompt2midi.evalkit import model
from prompt2midi.evalkit.runner import FAKE_SPEC
from prompt2midi.spec import MusicSpec

PPQ, BAR = 480, 1920  # 4/4


def midi(tracks, num=4, den=4):
    """tracks: list of [(abs_tick, channel, pitch, length_ticks)]; a meta track with the meter is added."""
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    meta = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0),
                           mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0), mido.MetaMessage("end_of_track", time=0)])
    mid.tracks.append(meta)
    for notes in tracks:
        ev = []
        for start, ch, p, ln in notes:
            ev += [(start, 1, mido.Message("note_on", channel=ch, note=p, velocity=80)), (start + ln, 0, mido.Message("note_off", channel=ch, note=p, velocity=0))]
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


def melody(pitches, step=480, start=0, ch=0):
    return [(start + i * step, ch, p, step // 2) for i, p in enumerate(pitches)]


def mat(tracks, spec=None, expect=None, **kw):
    return E.material_from_midi(midi(tracks, **kw), spec, expect)


SPEC = MusicSpec.model_validate(FAKE_SPEC)  # C major, 3 sections x 4 bars of 4/4


# ---------- melody_repetition ----------

def test_repetition_counts_windows_whose_interval_figure_returns():
    # pitches 60 62 64 65 60 62 64 65 67 -> intervals 2 2 1 -5 2 2 1 2 -> 6 windows, (2,2,1) twice -> 2/6
    assert E.melody_repetition(mat([melody([60, 62, 64, 65, 60, 62, 64, 65, 67])])) == 0.3333


def test_repetition_is_transposition_invariant_and_extremes():
    # 60 62 64 65 | 65 67 69 70 : both figures are (+2,+2,+1) shifted -> the windows (2,2,1) appear twice among 7 windows
    r = E.melody_repetition(mat([melody([60, 62, 64, 65, 65, 67, 69, 70])]))
    assert r == round(2 / 5, 4)                                    # intervals 2 2 1 0 2 2 1 -> windows: 221,210,102,022,221 -> 2/5
    assert E.melody_repetition(mat([melody([60] * 6)])) == 1.0     # a drone repeats everything
    assert E.melody_repetition(mat([melody([60, 67, 61, 70, 58, 66, 59])])) == 0.0
    assert E.melody_repetition(mat([melody([60, 62, 64])])) is None  # fewer than 4 notes


# ---------- melody_variation ----------

def test_variation_compares_bars_with_the_same_rhythm():
    bar0 = melody([60, 62, 64, 65], step=480, start=0)
    bar1 = melody([60, 62, 64, 67], step=480, start=BAR)           # same onsets, one pitch differs
    bar2 = [(2 * BAR, 0, 60, 480), (2 * BAR + 960, 0, 62, 480)]    # other rhythm: no partner
    assert E.melody_variation(mat([bar0 + bar1 + bar2])) == 0.25
    copy = melody([60, 62, 64, 65], start=BAR)
    assert E.melody_variation(mat([bar0 + copy])) == 0.0
    assert E.melody_variation(mat([bar0 + bar2])) is None          # no two bars share a rhythm
    assert E.melody_variation(mat([bar0])) is None


# ---------- step / leap ----------

def test_step_and_leap_ratio():
    # intervals |1| |3| |8| |0| |2| -> steps (1..2): 1 and 2 -> 2/5 ; leaps (>=5): 8 -> 1/5
    m = mat([melody([60, 61, 64, 72, 72, 74])])
    assert E.interval_step_ratio(m) == 0.4 and E.interval_leap_ratio(m) == 0.2
    assert E.interval_step_ratio(mat([melody([60])])) is None and E.interval_leap_ratio(mat([melody([60])])) is None
    down = mat([melody([72, 60])])                                  # direction does not matter
    assert E.interval_leap_ratio(down) == 1.0 and E.interval_step_ratio(down) == 0.0


# ---------- contour_diversity ----------

def test_contour_diversity_entropy():
    bars = (melody([60, 64, 62], step=640, start=0)                 # arch (peak inside)
            + melody([64, 62, 60], step=640, start=BAR)             # descending
            + melody([60, 62, 64], step=640, start=2 * BAR)         # ascending
            + melody([64, 60, 64], step=640, start=3 * BAR))        # valley
    assert E.contour_diversity(mat([bars])) == 0.8614               # ln4 / ln5
    same = melody([60, 62, 64], step=640, start=0) + melody([61, 63, 65], step=640, start=BAR)
    assert E.contour_diversity(mat([same])) == 0.0                  # two ascending bars: one shape
    assert E.contour_diversity(mat([melody([60, 62, 64], step=640)])) is None   # needs two bars
    flat = melody([60, 60, 60], step=640) + melody([62, 62, 62], step=640, start=BAR)
    assert E.contour_diversity(mat([flat])) == 0.0


def test_bar_contour_classes():
    shapes = ([60, 60, 60], [60, 64, 62], [64, 60, 64], [60, 62, 64], [64, 62, 60])
    assert [E._bar_contour(p) for p in shapes] == list(E.CONTOUR_CLASSES) == ["flat", "arch", "valley", "ascending", "descending"]


# ---------- final_is_tonic ----------

def test_final_is_tonic_from_spec_or_label():
    on_c = mat([melody([62, 64, 60])], SPEC)
    assert E.final_is_tonic(on_c) == 1.0
    assert E.final_is_tonic(mat([melody([60, 62])], SPEC)) == 0.0
    assert E.final_is_tonic(mat([melody([67, 72])], SPEC)) == 1.0           # pitch class, any octave
    lab = model.Expect.model_validate({"tonic": "D"})                          # no spec: the label's tonic is used
    assert E.final_is_tonic(mat([melody([60, 62])], None, lab)) == 1.0
    assert E.final_is_tonic(mat([melody([60, 62])])) is None                   # no spec, no label
    assert E.final_is_tonic(mat([[(0, 1, 60, 240)]], SPEC)) is None            # no melody channel


# ---------- section gaps ----------

def test_section_density_and_register_gap():
    sec = lambda i: i * 4 * BAR  # section starts (4 bars = 16 beats)
    a = melody([60] * 8, step=960, start=sec(0))                    # 8 notes / 16 beats = 0.5
    b = melody([72] * 16, step=480, start=sec(1))                   # 16 notes / 16 beats = 1.0
    c = melody([60] * 8, step=960, start=sec(2))                    # 0.5
    m = mat([a + b + c], SPEC)
    assert E.section_density_gap(m) == 0.5
    assert E.section_register_gap(m) == 12.0                         # mean pitch 60 / 72 / 60
    assert E.section_density_gap(mat([a + b + c])) is None           # no spec -> no section borders
    assert E.section_register_gap(mat([a + b + c])) is None
    drums_only_section = mat([a + b + c + [(sec(0), 9, 36, 100)] * 3], SPEC)
    assert E.section_density_gap(drums_only_section) == 0.5          # drums are not counted


# ---------- chord_voice_distance ----------

def chord(start, pitches, ch=2):
    return [(start, ch, p, 960) for p in pitches]


def test_chord_voice_distance_pairs_voices_from_the_bottom():
    c, f = [60, 64, 67], [60, 65, 69]
    two = chord(0, c) + chord(BAR, f)
    assert E.chord_voice_distance(mat([two])) == 1.0                        # (0 + 1 + 2) / 3
    assert E.chord_voice_distance(mat([chord(0, c) + chord(BAR // 2, c) + chord(BAR, f)])) == 1.0   # repeated chord is merged
    assert E.chord_voice_distance(mat([chord(0, c) + chord(BAR, f) + chord(2 * BAR, c)])) == 1.0     # C->F 1.0, F->C 1.0
    wide = chord(0, [48, 60, 64, 67]) + chord(BAR, f)                       # 4 vs 3 voices: first 3 pairs: 12, 5, 5
    assert E.chord_voice_distance(mat([wide])) == round(22 / 3, 4)
    assert E.chord_voice_distance(mat([chord(0, c)])) is None               # only one chord
    assert E.chord_voice_distance(mat([chord(0, c) + chord(BAR, c)])) is None   # same chord twice = no change
    assert E.chord_voice_distance(mat([chord(0, c, 3) + chord(BAR, f, 3)])) == 1.0   # pad channel is the fallback
    assert E.chord_voice_distance(mat([melody([60, 62, 64, 65])])) is None   # no chord channel


# ---------- off_scale_rate ----------

def test_off_scale_rate_label_first_then_spec():
    notes = melody([60, 62, 64, 65])                                         # all inside C major
    assert E.off_scale_rate(mat([notes], SPEC)) == 0.0                       # spec key C major
    assert E.off_scale_rate(mat([melody([60, 62, 61, 64])], SPEC)) == 0.25   # C#(61) is outside C major
    lab = model.Expect.model_validate({"tonic": "D", "mode": "major"})       # the label wins: D major = D E F# G A B C#
    assert E.off_scale_rate(mat([notes], SPEC, lab)) == 0.5                  # C(60) and F(65) are outside D major
    assert E.off_scale_rate(mat([notes])) is None                            # neither
    assert E.off_scale_rate(mat([[(0, 9, 36, 240)]], SPEC)) is None          # drums only


# ---------- measure() ----------

def test_measure_returns_every_indicator_and_none_for_bad_input():
    assert list(E.measure(None)) == [i.name for i in E.INDICATORS] and set(E.measure(None).values()) == {None}
    assert set(E.measure(b"not midi", SPEC).values()) == {None}
    got = E.measure(midi([melody([60, 62, 64, 65, 60, 62, 64, 65, 67]) + chord(0, [60, 64, 67]) + chord(BAR, [60, 65, 69])]), SPEC)
    assert got["melody_repetition"] == 0.3333 and got["chord_voice_distance"] == 1.0 and got["final_is_tonic"] == 0.0
    assert all(v is None or isinstance(v, float) for v in got.values())


def test_measure_is_deterministic():
    data = midi([melody([60, 62, 64, 65, 60, 62, 64, 65, 67]) + chord(0, [60, 64, 67])])
    assert E.measure(data, SPEC) == E.measure(data, SPEC)


def test_indicators_have_a_proxy_and_limits_docstring():
    for i in E.INDICATORS:
        doc = i.fn.__doc__ or ""
        assert "Stands in for" in doc and "Limits" in doc, i.name
