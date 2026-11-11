"""PR5 of docs/compiler-v2.md: engine="v2" playing-chord list (substitution, cadences, closing tonic) and the bass line.

These tests pin the rules, not the sound."""
from __future__ import annotations

import io
import json
from pathlib import Path

import mido
import pytest

from prompt2midi import spec as S
from prompt2midi.compiler import PPQ, compile_spec
from prompt2midi.compiler import harmony_v2 as H
from prompt2midi.compiler import melody_v2 as M
from prompt2midi.compiler.voicing_v2 import chord_pcs
from prompt2midi.compiler.render import _slots, _ticks
from prompt2midi.compiler.rng import Rng
from prompt2midi.music import effective_register
from prompt2midi.providers import RuleProvider
from prompt2midi.spec import TIME_SIGNATURES, Role, beats_per_bar, effective_seed, parse_chord
from prompt2midi.validate import check_midi, check_theory, validate_spec
from prompt2midi.validate.theory import chord_is_diatonic

SMOKE = [p["text"] for p in json.loads((Path(__file__).parent / "fixtures" / "smoke_set.json").read_text(encoding="utf-8"))["prompts"]]


@pytest.fixture(scope="module")
def base():
    return validate_spec(RuleProvider().generate_spec(SMOKE[0]))[0]


def _spec(base, tonic="C", mode="major", sections=None, engine="v2", sig=(4, 4), **upd):
    d = base.model_dump(mode="json", by_alias=True, exclude_none=True)
    d["engine"] = engine
    if not any(t["role"] == "bass" for t in d["tracks"]):
        d["tracks"] = d["tracks"] + [{"role": "bass", "instrument": "electric_bass"}]
    d["key"] = {"tonic": tonic, "mode": mode}
    d["time_signature"] = list(sig)
    tmpl = d["sections"][0]
    d["sections"] = [dict(tmpl, name=f"s{i}", **sec) for i, sec in enumerate(sections)]
    d.update(upd)
    return S.MusicSpec.model_validate(d)


def _playing(sp, seed=None):
    return H.playing_slots(sp, list(_slots(sp)), effective_seed(sp) if seed is None else seed)


def _names(slots):
    return [s[2] for s in slots]


def _sec(chords, bars=None, cpb=1, energy=0.8):
    return {"chords": chords, "bars": bars or len(chords) // cpb, "chords_per_bar": cpb, "energy": energy}


A = ["C", "F", "C", "F"]    # I IV I IV in C major (eligible for I->vi and IV->ii)


# ------------------------------------------------------------------------------------------- theory helpers


def test_diatonic_chords_by_mode():
    base = validate_spec(RuleProvider().generate_spec(SMOKE[0]))[0]
    c = _spec(base, "C", "major", [_sec(A)])
    assert [H.diatonic_chord(c, d) for d in range(1, 8)] == ["C", "Dm", "Em", "F", "G", "Am", "Bdim"]
    assert H.diatonic_chord(c, 5, seventh=True) == "G7" and H.diatonic_chord(c, 1, seventh=True) == "Cmaj7"
    a = _spec(base, "A", "minor", [_sec(["Am", "Dm", "Am", "Dm"])])
    assert [H.diatonic_chord(a, d) for d in range(1, 8)] == ["Am", "Bdim", "C", "Dm", "Em", "F", "G"]
    assert H.diatonic_chord(a, 5, seventh=True) == "Em7"           # natural minor: v7 is minor, so no V7 there
    assert H.diatonic_chord(_spec(base, "A", "harmonic_minor", [_sec(["Am", "Dm", "Am", "Dm"])]), 5, seventh=True) == "E7"
    assert H.degree_of(c, "F") == 4 and H.degree_of(c, "Fm") is None and H.degree_of(c, "Am") == 6


def test_recurrence_counts_earlier_sections_with_the_same_progression(base):
    sp = _spec(base, sections=[_sec(A), _sec(["C", "G", "Am", "F"]), _sec(A), _sec(A)])
    assert H.recurrence(sp) == [0, 0, 1, 2]
    assert [H.substitution_probability(r) for r in (0, 1, 2, 5)] == [H.SUB_PROB[0], H.SUB_PROB[1], H.SUB_PROB[2], H.SUB_PROB[2]]
    assert H.SUB_PROB[0] <= H.SUB_PROB[1] <= H.SUB_PROB[2]


# ------------------------------------------------------------------------------------------- substitution


def _count_subs(sp, seed):
    """Substitutions per section: I -> vi and IV -> ii only (cadence changes are counted elsewhere)."""
    per = [0] * len(sp.sections)
    for s, p in zip(_slots(sp), _playing(sp, seed)):
        per[s[3]] += (H.degree_of(sp, s[2]), H.degree_of(sp, p[2])) in ((1, 6), (4, 2))
    return per


def test_substitution_stays_inside_the_function_group_by_table(base):
    for tonic, mode, prog in (("C", "major", A), ("A", "minor", ["Am", "Dm", "Am", "Dm"]), ("D", "mixolydian", ["D", "G", "D", "G"]),
                              ("G", "lydian", ["G", "C", "G", "C"])):
        sp = _spec(base, tonic, mode, [_sec(prog)] * 5 + [_sec(prog, energy=0.4)])
        seen = set()
        for seed in range(40):
            for s, o in zip(_playing(sp, seed), _slots(sp)):
                if s[2] != o[2]:
                    seen.add((H.degree_of(sp, o[2]), H.degree_of(sp, s[2])))
                    assert chord_is_diatonic(sp, s[2]), (tonic, mode, s[2])
        # a changed chord is one of the table's (from -> to) degree pairs, or a cadence chord (IV/ii -> V, V -> V7, closing tonic)
        allowed = {(1, 6), (4, 2), (4, 5), (2, 5), (None, 5), (None, 1), (5, None), (4, 1), (6, 1), (2, 1), (5, 1)}
        assert seen <= allowed | {(d, None) for d in range(1, 8)}, (tonic, mode, seen)


def test_first_slot_and_last_section_are_not_substituted(base):
    prog = ["C", "F", "C", "F", "C", "F", "C", "C"]
    sp = _spec(base, sections=[_sec(prog)] * 5)
    orig = _names(list(_slots(sp)))
    n_last = len(prog)
    saved = H.SUB_PROB
    H.SUB_PROB = (1.0, 1.0, 1.0)   # force: every eligible chord is substituted
    try:
        play = _names(_playing(sp, 3))
    finally:
        H.SUB_PROB = saved
    assert play[0] == orig[0], "the first chord of the piece is the anchor"
    assert play[-n_last:] == orig[-n_last:], "the last section is played as written (it already ends on the tonic, no V before it)"
    assert play != orig


def test_substitution_probability_is_monotone_in_the_recurrence_over_100_seeds(base):
    sp = _spec(base, sections=[_sec(A)] * 5 + [_sec(A, energy=0.4)])
    totals = [0] * len(sp.sections)
    for seed in range(100):
        for i, n in enumerate(_count_subs(sp, seed)):
            totals[i] += n
    # sections 0..4 have recurrence 0,1,2,3,4 (capped at 2): never substituted at 0, then non-decreasing in expectation
    assert totals[0] == 0
    assert totals[1] > 0
    assert totals[1] < totals[2], totals
    assert abs(totals[2] - totals[3]) <= 0.25 * totals[2] and abs(totals[3] - totals[4]) <= 0.25 * totals[3]   # capped from 2 on
    assert totals[5] == 0 or totals[5] < totals[2]


def test_seventh_chord_specs_are_skipped_but_keep_their_own_tonic_closing(base):
    blues = ["E7", "A7", "E7", "B7"]
    sp = _spec(base, "E", "major", [_sec(blues)] * 3)
    play = _names(_playing(sp, 1))
    orig = _names(list(_slots(sp)))
    assert play[:-1] == orig[:-1]                      # no substitution, no half cadence, no V7 in a spec that already uses sevenths
    assert play[-1] == "E7"                            # closes on the tonic chord in the spec's own colour (not Emaj7)
    jazz = _spec(base, "C", "major", [_sec(["Dm7", "G7", "Cmaj7", "Am7"])] * 2)
    assert _names(_playing(jazz, 1))[-1] == "Cmaj7"    # the spec's tonic colour again
    only = _spec(base, "C", "major", [_sec(["Dm7", "G7", "Em7", "Am7"])] * 2)
    assert _names(_playing(only, 1))[-1] == "Cmaj7"    # no tonic chord in the spec: the diatonic seventh of degree 1


# ------------------------------------------------------------------------------------------- cadences


def test_half_cadence_closing_and_v7(base):
    sp = _spec(base, sections=[_sec(["C", "G", "Am", "F"]), _sec(["C", "F", "G", "C"]), _sec(["C", "G", "Am", "F"])])
    play = _names(_playing(sp, 0))
    assert play[3] == "G"                              # section 1 ends on IV: half cadence -> V (triad, not V7)
    assert play[6] == "G7" and play[7] == "C"          # section 2 ends V, I: V -> V7
    assert play[11] == "C"                             # the piece ends on the tonic chord although the spec ends on IV
    assert play[0] == "C"
    # a spec that already ends on the tonic is left alone; a tonic with another quality in the same key too
    same = _spec(base, sections=[_sec(["C", "F", "G", "C"])] * 2)
    assert _names(_playing(same, 0))[-1] == "C"
    am = _spec(base, "A", "minor", [_sec(["Am", "Dm", "Em", "Am"])] * 2)
    assert _names(_playing(am, 0))[-1] == "Am" and "E7" not in _names(_playing(am, 0)) and "Em7" not in _names(_playing(am, 0))
    # in minor (natural minor v is minor) no V7 is invented
    assert not any(c.endswith("7") for c in _names(_playing(am, 0)))


def test_closing_slot_only_changes_the_last_slot(base):
    sp = _spec(base, sections=[_sec(["C", "G", "Am", "F"]), _sec(["C", "G", "Am", "F"])], tempo_bpm=100)
    orig, play = _names(list(_slots(sp))), _names(_playing(sp, 0))
    assert [i for i, (o, p) in enumerate(zip(orig, play)) if o != p] == [3, 7] and play[7] == "C"
    two = _spec(base, sections=[_sec(["C", "G", "Am", "F"], bars=2, cpb=2)] * 2)
    assert [i for i, (o, p) in enumerate(zip(_names(list(_slots(two))), _names(_playing(two, 0)))) if o != p] == [3, 7]


def test_playing_slots_keep_geometry_and_never_touch_the_spec(base):
    sp = _spec(base, sections=[_sec(A)] * 4)
    before = sp.model_dump_json()
    a, b = list(_slots(sp)), _playing(sp, 9)
    assert [(s[0], s[1], s[3]) for s in a] == [(s[0], s[1], s[3]) for s in b]
    assert sp.model_dump_json() == before
    assert _playing(sp, 9) == b                           # deterministic


def test_playing_chords_pass_check_theory_without_new_issues(base):
    for tonic, mode, prog in (("C", "major", A), ("A", "minor", ["Am", "Dm", "Am", "Dm"]), ("D", "dorian", ["Dm", "G", "Dm", "G"])):
        sp = _spec(base, tonic, mode, [_sec(prog)] * 4)
        old = len(check_theory(sp))
        for seed in range(30):
            play = _names(_playing(sp, seed))
            sp2 = _spec(base, tonic, mode, [_sec(play[i * 4:(i + 1) * 4]) for i in range(4)])
            issues = check_theory(sp2)
            assert not [i for i in issues if i.severity == "error"]
            assert len(issues) <= old, (tonic, mode, seed, play)


# ------------------------------------------------------------------------------------------- bass


class _Fixed:
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v


SCALE_C = frozenset({0, 2, 4, 5, 7, 9, 11})


def test_bass_plan_fifths_only_from_the_third_beat_max_two_and_approach():
    lo, hi, beat = 36, 55, PPQ
    root, fifth, nxt = 36, 43, 43                          # C -> G
    start, length = 0, 4 * beat
    p = H.bass_slot_plan(start, length, root, fifth, None, 0.9, beat, lo, hi, SCALE_C, _Fixed(0.0))
    assert [x[2] for x in p] == [root, root, fifth, fifth]            # beats 0,1 never a fifth; at most 2 in total
    p = H.bass_slot_plan(start, length, root, fifth, None, 0.9, beat, lo, hi, SCALE_C, _Fixed(0.99))
    assert [x[2] for x in p] == [root] * 4                            # the 0.6 chance not taken
    p = H.bass_slot_plan(start, length, root, fifth, nxt, 0.9, beat, lo, hi, SCALE_C, _Fixed(0.99))
    assert [x[2] for x in p][:3] == [root] * 3 and p[3][2] in (nxt - 1, nxt + 1, nxt - 2, nxt + 2)   # last beat: approach into G
    assert p[3][2] % 12 in SCALE_C
    # low energy: the root for the whole slot, no approach
    assert H.bass_slot_plan(start, length, root, fifth, nxt, 0.3, beat, lo, hi, SCALE_C, _Fixed(0.0)) == [(start, length, root)]
    # next chord has the same root: no approach; a one-beat slot: no approach
    assert H.bass_slot_plan(start, length, root, fifth, root, 0.9, beat, lo, hi, SCALE_C, _Fixed(0.99))[-1][2] == root
    assert H.bass_slot_plan(0, beat, root, fifth, nxt, 0.9, beat, lo, hi, SCALE_C, _Fixed(0.99)) == [(0, beat, root)]


def test_approach_note_is_diatonic_a_step_from_the_next_root_and_nearest_to_the_current_one():
    assert H.approach_note(43, 36, 36, 55, SCALE_C) == 41          # into G: F# and G# are out of the scale; F (-2) is nearer to the current C than A (+2)
    for nxt in range(36, 56):
        if nxt % 12 in SCALE_C:
            a = H.approach_note(nxt, 36, 36, 55, SCALE_C)
            assert a is None or (abs(a - nxt) in (1, 2) and a % 12 in SCALE_C and 36 <= a <= 55)
    # nothing diatonic within reach -> no approach note
    assert H.approach_note(36, 40, 36, 36, frozenset({0})) is None


def test_bass_rng_is_the_only_random_input_of_the_plan():
    a = H.bass_slot_plan(0, 4 * PPQ, 36, 43, 43, 0.9, PPQ, 36, 55, SCALE_C, Rng(5, "bass"))
    b = H.bass_slot_plan(0, 4 * PPQ, 36, 43, 43, 0.9, PPQ, 36, 55, SCALE_C, Rng(5, "bass"))
    assert a == b


# ------------------------------------------------------------------------------------------- the three tracks share one list


def _notes(midi, channel):
    out, open_ = [], {}
    for tr in mido.MidiFile(file=io.BytesIO(midi)).tracks:
        t = 0
        for m in tr:
            t += m.time
            if getattr(m, "channel", None) != channel:
                continue
            if m.type == "note_on" and m.velocity > 0:
                open_[m.note] = t
            elif m.type in ("note_off", "note_on") and m.note in open_:
                out.append((open_.pop(m.note), t, m.note))
    return sorted(out)


def test_melody_chords_and_bass_follow_the_same_playing_chords(base):
    sp = _spec(base, sections=[_sec(["C", "G", "Am", "F"]), _sec(["C", "F", "G", "C"]), _sec(["C", "G", "Am", "F"])])
    play = _playing(sp)
    assert _names(play) != _names(list(_slots(sp)))             # the playing list differs from the spec's chords
    midi = compile_spec(sp)
    chords_ch, bass_ch = _notes(midi, 2), _notes(midi, 1)
    for start, _length, chord, _si, _sec_ in play:
        root, _q = parse_chord(chord)
        assert {p % 12 for s, _e, p in chords_ch if s == start} == set(chord_pcs(chord)), chord            # chord track: the playing chord
        first_bass = min((n for n in bass_ch if n[0] == start), default=None)
        assert first_bass is not None and first_bass[2] % 12 == root, (chord, first_bass)                    # bass: its root first
    tr = next(t for t in sp.tracks if t.role is Role.melody)
    got = M.build_melody(sp, tr, effective_seed(sp), play, _ticks(beats_per_bar(sp)), PPQ, lambda e, r: 80)
    starts = [s[0] for s in play]
    for n in got:                                                # melody: chord tones of the playing chord at each chord change
        i = max(j for j, s in enumerate(starts) if s <= n.start)
        if n.start == starts[i]:
            assert n.pitch % 12 in M.chord_tones(play[i][2])


def test_engine_none_v1_are_unchanged_by_the_playing_list(base):
    sp = _spec(base, sections=[_sec(["C", "G", "Am", "F"])] * 3, engine="v1")
    none = _spec(base, sections=[_sec(["C", "G", "Am", "F"])] * 3, engine="v1").model_copy(update={"engine": None})
    assert compile_spec(sp) == compile_spec(none)


def test_blues_melody_keeps_inside_the_key_while_the_chords_keep_their_sevenths():
    """e09 (blues in E major): v2's melody used to land on the out-of-key 7ths of E7/A7 (D, G natural)."""
    sp = validate_spec(RuleProvider().generate_spec("Blues shuffle with organ and electric guitar in E major at 92 BPM"))[0]
    sp = sp.model_copy(update={"engine": "v2"})
    midi = compile_spec(sp)
    scale = {(S.PITCH_CLASS[sp.key.tonic.value] + i) % 12 for i in S.MODE_INTERVALS[sp.key.mode.value]}
    mel = _notes(midi, 0)
    assert mel and all(p % 12 in scale for _s, _e, p in mel)
    chords = {p % 12 for _s, _e, p in _notes(midi, 2)} | {p % 12 for _s, _e, p in _notes(midi, 3)}
    assert chords - scale, "the chord tracks keep the blues sevenths (they are written in the spec)"


# ------------------------------------------------------------------------------------------- determinism / structure


@pytest.mark.parametrize("sig", TIME_SIGNATURES, ids=lambda s: f"{s[0]}-{s[1]}")
def test_all_time_signatures_bar_counts_chords_per_bar(base, sig):
    for secs in ([_sec(["C"], 1)], [_sec(["C", "G"], 2, 2)], [_sec(A), _sec(A)], [_sec(A, 3), _sec(["C", "G"], 1, 2)],
                 [_sec(A, 32)], [_sec(A, 32), _sec(A, 32)], [_sec(A, 32)] * 2 + [_sec(A, 4)], [_sec(A, 16)] * 3 + [_sec(["C"], 16, cpb=2, energy=0.2)]):
        sp = _spec(base, sections=secs, sig=sig)
        m = compile_spec(sp)
        assert not check_midi(m) and m == compile_spec(sp)
        v1 = compile_spec(sp.model_copy(update={"engine": None}))
        assert mido.MidiFile(file=io.BytesIO(m)).length == pytest.approx(mido.MidiFile(file=io.BytesIO(v1)).length)
        lo, hi = effective_register(next(t for t in sp.tracks if t.role is Role.bass))
        assert all(lo <= p <= hi for _s, _e, p in _notes(m, 1))


def test_smoke_specs_deterministic_check_midi_drums_untouched(base):
    r = RuleProvider()
    for text in SMOKE:
        sp = validate_spec(r.generate_spec(text))[0]
        v2 = sp.model_copy(update={"engine": "v2"})
        m = compile_spec(v2)
        assert m == compile_spec(v2) and not check_midi(m)
        assert _notes(m, 9) == _notes(compile_spec(sp), 9)
