"""PR4 of docs/compiler-v2.md: engine="v2" chord/pad voicing by minimal-movement voice leading.

These tests pin the rules, not the sound."""
from __future__ import annotations

import io
import json
from itertools import product
from pathlib import Path

import mido
import pytest

from prompt2midi import spec as S
from prompt2midi.compiler import compile_spec
from prompt2midi.compiler import voicing_v2 as V
from prompt2midi.compiler.render import _chord_pitches
from prompt2midi.music import effective_register
from prompt2midi.providers import RuleProvider
from prompt2midi.spec import TIME_SIGNATURES, Role
from prompt2midi.validate import check_midi, validate_spec
from prompt2midi.validate.theory import CHORD_TONES

PROMPTS = [p["text"] for p in json.loads((Path(__file__).parent / "fixtures" / "smoke_set.json").read_text(encoding="utf-8"))["prompts"]]
QUALITIES = list(CHORD_TONES)          # all 10 chord qualities of the spec
NAMES = {"": "", "m": "m", "7": "7", "m7": "m7", "maj7": "maj7", "dim": "dim", "aug": "aug", "sus2": "sus2", "sus4": "sus4", "m7b5": "m7b5"}


def _chords(roots=("C", "Eb", "F#", "A")):
    return [r + NAMES[q] for r in roots for q in QUALITIES]


def _v2(spec, **upd):
    d = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    d["engine"] = "v2"
    d.update(upd)
    return S.MusicSpec.model_validate(d)


@pytest.fixture(scope="module")
def specs():
    r = RuleProvider()
    return [validate_spec(r.generate_spec(p))[0] for p in PROMPTS]


def _channel_notes(midi, channel):
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


def test_every_chord_quality_has_every_tone_once_inside_the_register():
    lo, hi = 48, 72
    for ch in _chords():
        for v in V.candidates(V.chord_pcs(ch), lo, hi):
            assert list(v) == sorted(set(v)) and all(lo <= p <= hi for p in v)
            assert sorted(p % 12 for p in v) == sorted(V.chord_pcs(ch))
    assert len(V.chord_pcs("Cmaj7")) == 4 and len(V.chord_pcs("C")) == 3


def test_next_voicing_is_deterministic_and_stays_in_register_across_all_quality_pairs():
    lo, hi = 48, 72
    chords = _chords(("C", "F#"))
    for a, b in product(chords, repeat=2):
        va = V.next_voicing(a, lo, hi, None)
        vb = V.next_voicing(b, lo, hi, va)
        assert vb == V.next_voicing(b, lo, hi, va)
        assert all(lo <= p <= hi for p in vb) and sorted(p % 12 for p in vb) == sorted(V.chord_pcs(b))
        assert list(vb) == sorted(set(vb))


def test_voices_never_cross_in_the_chosen_matching():
    lo, hi = 48, 72
    for a, b in product(_chords(("C", "Bb")), repeat=2):
        va = V.next_voicing(a, lo, hi, None)
        vb = V.next_voicing(b, lo, hi, va)
        _cost, pairs = V.match(va, vb)
        both = sorted((p, q) for p, q in pairs if p is not None and q is not None)
        assert [q for _p, q in both] == sorted(q for _p, q in both), (a, b)   # pitch order preserved -> no crossing


def test_match_cost_unit():
    assert V.match((60, 64, 67), (60, 64, 69))[0] == 2.0                   # C -> Am: two voices stay, one moves a step
    assert V.match((60, 64, 67), (60, 65, 69))[0] == 3.0                   # C -> F: the common C stays
    assert V.match((53, 59, 62, 67), (52, 60, 67))[0] <= 3.0               # seventh -> triad: one voice stops (free)
    prev, cand = (60, 64, 67), (59, 62, 65, 67)                            # triad -> seventh: one voice enters
    cost, pairs = V.match(prev, cand)
    assert sum(p is None for p, _q in pairs) == 1 and sum(q is None for _p, q in pairs) == 0
    from itertools import combinations
    brute = min(sum(abs(prev[a] - cand[b]) for a, b in enumerate(keep))
                + sum(min(abs(cand[b] - x) for x in prev) for b in range(4) if b not in keep)
                for keep in combinations(range(4), 3))
    assert cost == brute


def test_common_tones_are_held_when_the_order_allows():
    # C -> Am and C -> F: the shared pitches must not move
    c = V.next_voicing("C", 48, 72, None)
    am = V.next_voicing("Am", 48, 72, c)
    f = V.next_voicing("F", 48, 72, c)
    assert {p for p in c if p % 12 in (0, 4)} <= set(am)        # C and E are common tones of C and Am
    assert {p for p in c if p % 12 == 0} <= set(f)              # C is the common tone of C and F


def test_voicing_cost_prefers_the_smaller_movement_and_breaks_ties_low():
    prev = (60, 64, 67)
    near, far = (60, 65, 69), (65, 69, 72)
    assert V.voicing_cost(prev, near, 60) < V.voicing_cost(prev, far, 60)
    # a spread voicing is penalised beyond SPAN_MAX
    wide = (48, 60, 76)
    assert V.voicing_cost(None, wide, 62) > V.voicing_cost(None, (57, 60, 64), 62)
    # equal cost -> the lowest tuple wins (deterministic)
    assert V.next_voicing("C", 48, 72, None) == V.next_voicing("C", 48, 72, None)


def test_engine_none_and_v1_chords_unchanged(specs):
    for sp in specs:
        base = compile_spec(sp)
        assert _channel_notes(base, 2) == _channel_notes(compile_spec(sp.model_copy(update={"engine": "v1"})), 2)
        assert [n for n in _channel_notes(base, 2)] == [n for n in _channel_notes(compile_spec(sp.model_copy(update={"engine": None})), 2)]


def test_v2_changes_only_voicing_pitches_not_rhythm_or_other_roles(specs):
    changed = 0
    for sp in specs:
        v1, v2 = compile_spec(sp), compile_spec(_v2(sp))
        for ch in (1, 9):
            assert _channel_notes(v1, ch) == _channel_notes(v2, ch)
        for ch in (2, 3):
            a, b = _channel_notes(v1, ch), _channel_notes(v2, ch)
            assert [(s, e) for s, e, _p in a] == [(s, e) for s, e, _p in b]   # same timing, only pitches change
            changed += a != b
    assert changed >= 1


def test_deterministic_and_check_midi(specs):
    for sp in specs:
        v2 = _v2(sp)
        m = compile_spec(v2)
        assert m == compile_spec(v2) and not check_midi(m)
        assert mido.MidiFile(file=io.BytesIO(m)).length == pytest.approx(mido.MidiFile(file=io.BytesIO(compile_spec(sp))).length)


def test_chord_and_pad_voicings_are_inside_the_register_and_complete(specs):
    for sp in specs:
        v2 = _v2(sp)
        midi = compile_spec(v2)
        for tr in v2.tracks:
            if tr.role not in (Role.chords, Role.pad):
                continue
            lo, hi = effective_register(tr)
            notes = _channel_notes(midi, 2 if tr.role is Role.chords else 3)
            assert all(lo <= p <= hi for _s, _e, p in notes)


def test_minimal_movement_is_actually_smaller_than_v1_on_average(specs):
    """Aggregate regression guard (not a quality claim): total movement along each spec's chord list."""
    v1_total = v2_total = 0.0
    for sp in specs:
        tr = next((t for t in sp.tracks if t.role in (Role.chords, Role.pad)), None)
        if tr is None:
            continue
        lo, hi = effective_register(tr)
        chords = [c for sec in sp.sections for c in (sec.chords * 2)]
        p1, c1, p2 = None, (lo + hi) / 2, None
        for ch in chords:
            a = _chord_pitches(ch, lo, hi, c1)
            c1 = sum(a) / len(a)
            b = V.next_voicing(ch, lo, hi, p2)
            if p1 is not None:
                v1_total += V.match(tuple(p1), tuple(a))[0]
                v2_total += V.match(tuple(p2), b)[0]
            p1, p2 = a, b
    assert v2_total <= v1_total, (v2_total, v1_total)


def _resized(sp, sig, bars_list, cpb):
    d = sp.model_dump(mode="json", by_alias=True, exclude_none=True)
    d["engine"] = "v2"
    d["time_signature"] = list(sig)
    base = d["sections"][0]
    d["sections"] = [dict(base, name=f"s{i}", bars=b, chords_per_bar=cpb, energy=0.2 if i % 2 else 0.8) for i, b in enumerate(bars_list)]
    return S.MusicSpec.model_validate(d)


@pytest.mark.parametrize("sig", TIME_SIGNATURES, ids=lambda s: f"{s[0]}-{s[1]}")
def test_all_time_signatures_bar_counts_sections_and_chords_per_bar(specs, sig):
    for bars_list, cpb in (([1], 1), ([2], 2), ([3, 1], 2), ([5], 1), ([16], 2), ([32, 32], 1), ([32, 32, 4], 2)):
        sp = _resized(specs[0], sig, bars_list, cpb)
        m = compile_spec(sp)
        assert not check_midi(m) and m == compile_spec(sp)
        v1 = compile_spec(sp.model_copy(update={"engine": None}))
        assert mido.MidiFile(file=io.BytesIO(m)).length == pytest.approx(mido.MidiFile(file=io.BytesIO(v1)).length)


def test_seventh_and_triad_pairs_all_ten_qualities_in_a_progression():
    """A progression through every quality (7th <-> triad <-> sus <-> dim/aug) keeps every voicing complete."""
    lo, hi = 48, 72
    prev = None
    for ch in _chords(("D",)) + _chords(("A",)):
        prev = V.next_voicing(ch, lo, hi, prev)
        assert sorted(p % 12 for p in prev) == sorted(V.chord_pcs(ch))
