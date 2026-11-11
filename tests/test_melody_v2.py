"""PR2 of docs/compiler-v2.md: engine="v2" melody (phrases, development, chord-tone correction).

These tests pin the *rules* (what the code is meant to do). They say nothing about whether the result sounds good."""
from __future__ import annotations

import io
import json
from itertools import groupby
from pathlib import Path

import mido
import pytest

from prompt2midi import spec as S
from prompt2midi.compiler import PPQ, compile_spec
from prompt2midi.compiler import melody_v2 as M
from prompt2midi.compiler.render import _slots, _ticks
from prompt2midi.music import effective_register
from prompt2midi.providers import RuleProvider
from prompt2midi.spec import Role, beats_per_bar, effective_seed
from prompt2midi.validate import check_midi, validate_spec

PROMPTS = [p["text"] for p in json.loads((Path(__file__).parent / "fixtures" / "smoke_set.json").read_text(encoding="utf-8"))["prompts"]]


def _v2(spec, seed=None):
    d = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    d["engine"] = "v2"
    if seed is not None:
        d["seed"] = seed
    return S.MusicSpec.model_validate(d)


@pytest.fixture(scope="module")
def specs():
    r = RuleProvider()
    return [validate_spec(r.generate_spec(p))[0] for p in PROMPTS]


def _notes(midi: bytes):
    out: dict[int, list[tuple[int, int, int]]] = {}
    for tr in mido.MidiFile(file=io.BytesIO(midi)).tracks:
        t, open_ = 0, {}
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                open_[(m.channel, m.note)] = t
            elif m.type in ("note_off", "note_on") and (m.channel, m.note) in open_:
                out.setdefault(m.channel, []).append((open_.pop((m.channel, m.note)), t, m.note))
    return {c: sorted(v) for c, v in out.items()}


def _melody(spec):
    tr = next((t for t in spec.tracks if t.role is Role.melody), None)
    if tr is None:
        return None, None
    got = M.build_melody(spec, tr, effective_seed(spec), list(_slots(spec)), _ticks(beats_per_bar(spec)), PPQ, lambda e, r: 80)
    return tr, got


def test_deterministic(specs):
    for sp in specs:
        v2 = _v2(sp)
        assert compile_spec(v2) == compile_spec(v2)
        assert compile_spec(v2, seed=5) == compile_spec(v2, seed=5)


def test_v1_and_none_unchanged_and_only_melody_differs(specs):
    differ = 0
    for sp in specs:
        v1, v2 = compile_spec(sp), compile_spec(_v2(sp))
        a, b = _notes(v1), _notes(v2)
        for ch in set(a) | set(b):
            if ch != 0:
                assert a.get(ch) == b.get(ch), f"channel {ch} must not change in PR2"
        differ += a.get(0) != b.get(0)
    assert differ >= len(specs) - 2, "v2 should change the melody of (almost) every spec"


def test_bar_structure_and_total_length_unchanged(specs):
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        bar = _ticks(beats_per_bar(sp))
        sec_ends, pos = [], 0
        for sec in sp.sections:
            pos += bar * sec.bars
            sec_ends.append(pos)
        for n in got:
            assert 0 <= n.start < n.end <= pos
        # nothing crosses a section border (phrases are cut at the section end like v1)
        for n in got:
            border = next(e for e in sec_ends if n.start < e)
            assert n.end <= border
        v1_len = mido.MidiFile(file=io.BytesIO(compile_spec(sp))).length
        assert mido.MidiFile(file=io.BytesIO(compile_spec(_v2(sp)))).length == pytest.approx(v1_len)


def test_strong_beats_and_chord_changes_are_chord_tones(specs):
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        slots = list(_slots(sp))
        starts = [s[0] for s in slots]
        bar = _ticks(beats_per_bar(sp))
        from prompt2midi.compiler import drums as D
        strong = {k * (PPQ // 2) for k in D.GRID[sp.time_signature][1]}
        for n in got:
            i = max(j for j, s in enumerate(starts) if s <= n.start)
            if n.start % bar in strong or n.start == starts[i]:
                assert n.pitch % 12 in M.chord_tones(slots[i][2]), (sp.title, n.start, n.pitch)


def test_weak_nonchord_tones_are_passing_neighbor_or_escape(specs):
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        slots = list(_slots(sp))
        starts = [s[0] for s in slots]
        ct = [n.pitch % 12 in M.chord_tones(slots[max(j for j, s in enumerate(starts) if s <= n.start)][2]) for n in got]
        for j, n in enumerate(got):
            if not ct[j]:
                assert 0 < j < len(got) - 1 and ct[j - 1] and ct[j + 1]
                assert M.nonchord_role(got[j - 1].pitch, n.pitch, got[j + 1].pitch) is not None


def test_register_respected(specs):
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        lo, hi = effective_register(tr)
        assert all(lo <= n.pitch <= hi for n in got)


def test_check_midi_passes_and_note_integrity(specs):
    for sp in specs:
        midi = compile_spec(_v2(sp))
        assert not check_midi(midi)
        assert not _notes(midi).get(0) or all(0 <= p <= 127 for _s, _e, p in _notes(midi)[0])


def test_motif_is_preserved_on_unmodified_passes(specs):
    agree = total = 0
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        signs = [(b > a) - (b < a) for a, b in zip(tr.motif.degrees, tr.motif.degrees[1:])]
        for _k, g in groupby(got, key=lambda n: (n.phrase, n.pass_k)):
            g = list(g)
            if len(g) != len(tr.motif.degrees) or g[0].op not in ("orig", "seq"):
                continue
            ps = [(b.pitch > a.pitch) - (b.pitch < a.pitch) for a, b in zip(g, g[1:])]
            agree += sum(x == y for x, y in zip(signs, ps))
            total += len(signs)
    assert total > 0
    # regression guard (not a quality claim): chord-tone correction must not flatten the motif contour
    assert agree / total >= 0.65, agree / total


def test_first_phrase_presents_the_motif_unshifted(specs):
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None or len(tr.motif.degrees) < 2:
            continue
        first = [n for n in got if n.phrase == 0 and n.pass_k == 0]
        assert first and first[0].op == "orig"


def test_phrase_helpers():
    assert M.phrase_lengths(8) == [4, 4]
    assert M.phrase_lengths(5) == [5]
    assert M.phrase_lengths(6) == [4, 2]
    assert M.phrase_lengths(2) == [2]
    assert M.phrase_lengths(9) == [4, 5]
    assert [M.phrase_role(i, 3) for i in range(3)] == ["presentation", "development", "cadence"]
    assert M.phrase_role(1, 2) == "cadence"


def test_nonchord_role_unit():
    assert M.nonchord_role(60, 62, 64) == "passing"
    assert M.nonchord_role(64, 62, 60) == "passing"
    assert M.nonchord_role(60, 62, 60) == "neighbor"
    assert M.nonchord_role(60, 61, 60) == "neighbor"
    assert M.nonchord_role(60, 62, 59) == "escape"  # step up, leap back down
    assert M.nonchord_role(60, 62, 66) is None      # step up then a leap further up: none of the three
    assert M.nonchord_role(60, 65, 67) is None      # leap into it
    assert M.nonchord_role(60, 60, 60) is None      # not a non-chord tone event
    assert M.nonchord_role(60, 63, 66) is None


def test_chord_tones_and_snap():
    assert M.chord_tones("C") == {0, 4, 7}
    assert M.chord_tones("Am") == {9, 0, 4}
    assert M._snap(61, frozenset({0, 4, 7}), 48, 84, None) == 60
    assert M._snap(62, frozenset({0, 4, 7}), 48, 84, 64) == 64   # tie (60/64): closer to previous
    assert M._snap(62, frozenset({0, 4, 7}), 48, 84, 60) == 60
    assert M._snap(66, frozenset({0, 4, 7}), 48, 84, None) in (67,)


# ---------------------------------------------------------------- PR3: cadence, contour, breath -----------------------

from prompt2midi.spec import TIME_SIGNATURES, parse_chord  # noqa: E402


def _resized(sp, sig, bars_list):
    d = sp.model_dump(mode="json", by_alias=True, exclude_none=True)
    d["engine"] = "v2"
    d["time_signature"] = list(sig)
    base = d["sections"][0]
    d["sections"] = [dict(base, name=f"s{i}", bars=b, energy=0.3 if i % 2 else 0.8) for i, b in enumerate(bars_list)]
    return S.MusicSpec.model_validate(d)


def _phrase_ends(sp):
    """[(section index, phrase end tick)] for every phrase, from the same phrase split the compiler uses."""
    bar = _ticks(beats_per_bar(sp))
    out, pos = [], 0
    for si, sec in enumerate(sp.sections):
        for ln in M.phrase_lengths(sec.bars):
            pos += ln * bar
            out.append((si, pos))
    return out


def test_complete_cadence_in_last_section_and_half_cadence_before(specs):
    checked = 0
    for sp in specs:
        sp = _v2(sp)
        tr, got = _melody(sp)
        if got is None or len(sp.sections) < 1:
            continue
        slots = list(_slots(sp))
        bar = _ticks(beats_per_bar(sp))
        total = sum(sec.bars for sec in sp.sections) * bar
        pos = 0
        for si, sec in enumerate(sp.sections):
            end = pos + sec.bars * bar
            last = [n for n in got if pos <= n.start < end][-1]
            chord = next(c for s0, ln, c, _i, _s in slots if s0 <= last.start < s0 + ln)
            info = M.chord_tones_info(chord)
            if si == len(sp.sections) - 1:
                tonic = S.PITCH_CLASS[sp.key.tonic.value]
                iv = S.MODE_INTERVALS[sp.key.mode.value]
                key = [tonic, (tonic + iv[2]) % 12, (tonic + iv[4]) % 12]
                want = next((pc for pc in key if pc in info.tones), info.root)   # tonic, third, fifth, else the chord root
                assert last.pitch % 12 == want, (sp.title, chord, last.pitch)
                assert last.end == total, "the final note is held to the very end of the last bar"
            else:
                assert last.pitch % 12 in info.tones
                if info.fifth is not None:
                    assert last.pitch % 12 == info.fifth or last.pitch % 12 == info.third
            pos = end
            checked += 1
    assert checked >= len(specs)


def test_every_phrase_ends_on_a_chord_tone_with_a_breath(specs):
    for sp in specs:
        sp = _v2(sp)
        tr, got = _melody(sp)
        if got is None:
            continue
        slots = list(_slots(sp))
        ends = _phrase_ends(sp)
        breath = int(M.BREATH_BEATS * PPQ)
        for k, (si, p_end) in enumerate(ends):
            inside = [n for n in got if n.end <= p_end and n.start < p_end and (k == 0 or n.start >= ends[k - 1][1])]
            if not inside:
                continue
            last = inside[-1]
            chord = next(c for s0, ln, c, _i, _s in slots if s0 <= last.start < s0 + ln)
            assert last.pitch % 12 in M.chord_tones(chord)
            is_final = k == len(ends) - 1
            if not is_final:
                room = p_end - breath - last.start >= int(M.MIN_NOTE_BEATS * PPQ)
                assert (not room) or p_end - last.end >= breath, (sp.title, k, p_end - last.end)


def test_no_leap_beyond_an_octave_and_leap_recovery_is_attempted(specs):
    leaps = recovered = 0
    for sp in specs:
        tr, got = _melody(_v2(sp))
        if got is None:
            continue
        for a, b in zip(got, got[1:]):
            assert abs(b.pitch - a.pitch) <= M.LEAP_MAX, (sp.title, a.start, a.pitch, b.pitch)
        for a, b, c in zip(got, got[1:], got[2:]):
            if abs(b.pitch - a.pitch) >= M.LEAP_MIN:
                leaps += 1
                recovered += (c.pitch - b.pitch) * (b.pitch - a.pitch) < 0
    # regression guard, not a quality claim: most leaps are answered by a move back (measured ~0.5 on the smoke set)
    assert leaps and recovered / leaps >= 0.35, (recovered, leaps)


@pytest.mark.parametrize("sig", TIME_SIGNATURES, ids=lambda s: f"{s[0]}-{s[1]}")
def test_all_time_signatures_and_bar_counts(specs, sig):
    base = specs[0]
    for bars_list in ([1], [2], [3], [4], [5], [7], [9], [16], [32], [32, 32], [3, 1, 6], [32] * 2 + [4]):
        sp = _resized(base, sig, bars_list)
        midi = compile_spec(sp)
        assert not check_midi(midi), (sig, bars_list)
        assert midi == compile_spec(sp)
        v1 = mido.MidiFile(file=io.BytesIO(compile_spec(sp.model_copy(update={"engine": None})))).length
        assert mido.MidiFile(file=io.BytesIO(midi)).length == pytest.approx(v1)
        tr, got = _melody(sp)
        if got is None:
            continue
        lo, hi = effective_register(tr)
        assert all(lo <= n.pitch <= hi for n in got)
        assert all(abs(b.pitch - a.pitch) <= M.LEAP_MAX for a, b in zip(got, got[1:]))
        total = sum(bars_list) * _ticks(beats_per_bar(sp))
        assert got[-1].end == total and all(n.end <= total for n in got)


def test_snap_keeps_the_motifs_direction_and_avoids_leaps():
    ct = frozenset({0, 4, 7})
    # raw 62 sits between 60 and 64 (a tie); the previous note 61 and a rising motif -> 64 (60 would fall)
    assert M._snap(62, ct, 48, 84, 61, want=1) == 64
    assert M._snap(62, ct, 48, 84, 63, want=-1) == 60
    # a far chord tone is not chosen when a near one exists
    assert abs(M._snap(70, ct, 48, 84, 67, want=0) - 67) <= 3


def test_cadence_pitch_unit():
    c = M.chord_tones_info("C")
    assert (c.root, c.third, c.fifth) == (0, 4, 7)
    assert M._cadence_pitch(c, 62, 64, False, 48, 84) == 67              # half cadence: the fifth
    d = M.chord_tones_info("Bdim")
    assert M._cadence_pitch(d, 62, None, False, 48, 84) % 12 in d.tones  # a chord without a perfect fifth still gives a chord tone
    # complete cadence: tonic if a chord tone; else the key's third, then fifth; else the chord root
    C_MAJOR = (0, 4, 7)
    def end(chord, cur=62, key=C_MAJOR):
        return M._cadence_pitch(M.chord_tones_info(chord), cur, None, True, 48, 84, key) % 12
    assert end("F") == 0        # IV  (F A C): the tonic C is a chord tone
    assert end("Am") == 0       # vi  (A C E): the tonic again
    assert end("C") == 0        # I
    assert end("G") == 7        # V   (G B D): no tonic, no key third (E); the key fifth G
    assert end("Em") == 4       # iii (E G B): no tonic; the key third E comes before the fifth G
    assert end("Dm") == 2       # ii  (D F A): none of C/E/G -> the chord root
    assert end("Bdim") == 11    # vii: none of C/E/G -> the chord root
    # minor key (A minor: tonic A, third C, fifth E): IV = Dm (D F A) -> the tonic A
    assert end("Dm", key=(9, 0, 4)) == 9
    assert end("G", key=(9, 0, 4)) == 7     # VII (G B D): none -> the root
    # the closest octave to the line is used
    assert M._cadence_pitch(M.chord_tones_info("F"), 70, None, True, 48, 84, C_MAJOR) == 72
    assert M._cadence_pitch(M.chord_tones_info("F"), 62, None, True, 48, 84, C_MAJOR) == 60
