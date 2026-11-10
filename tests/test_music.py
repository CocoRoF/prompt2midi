import pytest

from prompt2midi import spec as S
from prompt2midi.music import degree_offset, effective_register, motif_pitches, shift_degree
from prompt2midi.validate import validate_schema


def mk(d):
    return validate_schema(d)[0]


def test_degree_offsets_in_c_major_and_c_minor(spec_dict):
    spec_dict["key"] = {"tonic": "C", "mode": "major"}
    s = mk(spec_dict)
    assert [degree_offset(s, d) for d in (1, 2, 3, 4, 5, 6, 7, 8, 14)] == [0, 2, 4, 5, 7, 9, 11, 12, 23]
    assert [degree_offset(s, d) for d in (-1, -2, -7)] == [-1, -3, -12]
    spec_dict["key"]["mode"] = "minor"
    assert degree_offset(mk(spec_dict), 3) == 3 and degree_offset(mk(spec_dict), -1) == -2


def test_shift_degree_skips_zero():
    assert shift_degree(1, -1) == -1 and shift_degree(2, -2) == -1 and shift_degree(-1, 1) == 1
    assert shift_degree(3, 2) == 5 and shift_degree(-3, -1) == -4


def test_motif_below_the_tonic_fits_and_is_placed_in_the_register(spec_dict):
    spec_dict["tracks"][0]["motif"] = {"degrees": [-1, 1, 3], "rhythm": ["q", "q", "q"]}
    spec_dict["tracks"][0]["register"] = [60, 84]
    s = mk(spec_dict)
    p = motif_pitches(s, s.tracks[0])
    assert p is not None and p[0] < p[1] < p[2] and min(p) >= 60 and max(p) <= 84
    assert (p[1] - p[0]) in (1, 2)  # leading tone / subtonic sits just below the tonic


def test_effective_register_clips_defaults_to_the_instrument(spec_dict):
    spec_dict["tracks"][0]["instrument"] = "trumpet"
    spec_dict["tracks"][0].pop("register")
    s = mk(spec_dict)
    assert effective_register(s.tracks[0]) == (60, 82)
    assert effective_register(s.tracks[2]) is None  # drums


def test_all_default_registers_are_inside_all_instrument_ranges_or_reported():
    # documents which role/instrument pairs have no usable default register (span < 12)
    narrow = []
    for role in ("melody", "bass", "chords", "pad"):
        for ins in S.Instrument:
            lo, hi = S.DEFAULT_REGISTER[role]
            ilo, ihi = S.INSTRUMENT_RANGE[ins.value]
            if min(hi, ihi) - max(lo, ilo) < 12:
                narrow.append((role, ins.value))
    assert ("bass", "violin") in narrow and ("melody", "electric_bass") in narrow
    assert ("melody", "piano") not in narrow and ("bass", "electric_bass") not in narrow
