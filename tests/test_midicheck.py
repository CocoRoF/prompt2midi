import struct

import mido
import pytest

from prompt2midi.spec import effective_seed
from prompt2midi.validate import check_midi, validate_schema
from tests.midi_builder import PPQ, good_midi, insert_raw, load, save, strip_end_of_track


@pytest.fixture
def spec(spec_dict):
    s, issues = validate_schema(spec_dict)
    assert not issues
    return s


def codes(issues, severity="error"):
    return {i.code for i in issues if i.severity == severity}


def test_good_midi_passes(spec):
    assert check_midi(good_midi(spec), spec) == []


def test_good_midi_passes_without_spec(spec):
    assert check_midi(good_midi(spec)) == []


# ---- broken bytes / structure ----

def test_empty_bytes():
    assert "truncated" in codes(check_midi(b""))


def test_garbage_bytes():
    assert codes(check_midi(b"this is definitely not midi at all"))


def test_wrong_magic(spec):
    data = bytearray(good_midi(spec))
    data[0:4] = b"RIFF"
    assert "bad_header" in codes(check_midi(bytes(data), spec))


def test_truncated_file(spec):
    data = good_midi(spec)
    issues = check_midi(data[: len(data) - 7], spec)
    assert codes(issues) & {"truncated", "unparseable"}


def test_trailing_garbage(spec):
    assert "trailing_bytes" in codes(check_midi(good_midi(spec) + b"\x00\x01", spec))


def test_track_count_mismatch(spec):
    data = bytearray(good_midi(spec))
    data[10:12] = struct.pack(">H", 7)  # header claims 7 tracks
    assert "track_count" in codes(check_midi(bytes(data), spec))


def test_zero_ticks_per_beat(spec):
    data = bytearray(good_midi(spec))
    data[12:14] = struct.pack(">H", 0)
    assert "bad_header" in codes(check_midi(bytes(data), spec))


def test_smpte_division_rejected(spec):
    data = bytearray(good_midi(spec))
    data[12:14] = struct.pack(">H", 0xE728)
    assert "bad_header" in codes(check_midi(bytes(data), spec))


def test_unsupported_format(spec):
    data = bytearray(good_midi(spec))
    data[8:10] = struct.pack(">H", 2)
    assert "bad_header" in codes(check_midi(bytes(data), spec))


def test_missing_end_of_track(spec):
    data = strip_end_of_track(good_midi(spec), 1)
    assert "no_end_of_track" in codes(check_midi(data, spec))


def test_end_of_track_in_the_middle(spec):
    # mido silently drops everything after the first EndOfTrack, so only the raw walk can see this
    data = insert_raw(good_midi(spec), 1, 4, b"\x00\xff\x2f\x00")  # after the first note_on (4 bytes)
    assert "bad_end_of_track" in codes(check_midi(data, spec))


def test_malformed_track_bytes(spec):
    data = insert_raw(good_midi(spec), 1, 4, b"\x00\xff\x51\x7f")  # meta event announcing 127 bytes
    assert codes(check_midi(data, spec)) & {"malformed_track", "unparseable"}


# ---- notes ----

def test_note_on_without_note_off(spec):
    mid = load(good_midi(spec))
    del mid.tracks[1][1]  # remove the note_off
    assert "unterminated_note" in codes(check_midi(save(mid), spec))


def test_note_off_without_note_on(spec):
    mid = load(good_midi(spec))
    del mid.tracks[1][0]  # remove the note_on
    assert "unmatched_note_off" in codes(check_midi(save(mid), spec))


def test_note_value_over_127_in_raw_bytes(spec):
    data = bytearray(good_midi(spec))
    i = data.index(bytes([0x90, 60, 80]))
    data[i + 1] = 200  # data byte with high bit set: invalid in a running-status stream
    assert codes(check_midi(bytes(data), spec))


def test_note_on_velocity_zero_counts_as_note_off(spec):
    mid = load(good_midi(spec))
    mid.tracks[1][1] = mido.Message("note_on", channel=0, note=60, velocity=0, time=PPQ)
    assert check_midi(save(mid), spec) == []


def test_zero_length_note_is_only_a_warning(spec):
    mid = load(good_midi(spec))
    mid.tracks[1][1].time = 0
    issues = check_midi(save(mid), spec)
    assert codes(issues) == set() and "zero_length_note" in codes(issues, "warning")
    # the note_off moved to tick 0, so the end_of_track delta must still add up
    assert not [i for i in issues if i.severity == "error"]


def test_no_notes_at_all(spec):
    mid = load(good_midi(spec))
    mid.tracks = [mid.tracks[0]]
    assert "no_notes" in codes(check_midi(save(mid), spec))


# ---- spec agreement ----

def test_wrong_tempo(spec):
    mid = load(good_midi(spec))
    mid.tracks[0][0].tempo = mido.bpm2tempo(120)
    assert "tempo_mismatch" in codes(check_midi(save(mid), spec))


def test_missing_tempo(spec):
    mid = load(good_midi(spec))
    del mid.tracks[0][0]
    assert "tempo_missing" in codes(check_midi(save(mid), spec))


def test_late_tempo(spec):
    mid = load(good_midi(spec))
    mid.tracks[0][0].time = 10
    mid.tracks[0][-1].time -= 10
    assert "tempo_late" in codes(check_midi(save(mid), spec))


def test_wrong_time_signature(spec):
    mid = load(good_midi(spec))
    mid.tracks[0][1].numerator = 3
    assert "signature_mismatch" in codes(check_midi(save(mid), spec))


def test_missing_time_signature(spec):
    mid = load(good_midi(spec))
    del mid.tracks[0][1]
    assert "signature_missing" in codes(check_midi(save(mid), spec))


def test_too_short(spec):
    mid = load(good_midi(spec))
    for t in mid.tracks:
        t[-1].time -= PPQ * 4
    assert "length_mismatch" in codes(check_midi(save(mid), spec))


def test_too_long(spec):
    mid = load(good_midi(spec))
    for t in mid.tracks:
        t[-1].time += PPQ * 4
    assert "length_mismatch" in codes(check_midi(save(mid), spec))


def test_note_past_the_end(spec):
    mid = load(good_midi(spec))
    mid.tracks[1][1].time = PPQ * 4 * 8 + PPQ  # note_off after the piece end
    mid.tracks[1][-1].time = 0
    assert "note_past_end" in codes(check_midi(save(mid), spec))


def test_drums_expected_but_missing(spec):
    assert "drums_missing" in codes(check_midi(good_midi(spec, drums=False), spec))


def test_drums_present_but_not_in_spec(spec_dict):
    spec_dict["tracks"] = [t for t in spec_dict["tracks"] if t["role"] != "drums"]
    s, _ = validate_schema(spec_dict)
    assert "unexpected_drums" in codes(check_midi(good_midi(s, drums=True), s))


def test_other_time_signatures_are_checked(spec_dict):
    spec_dict["time_signature"] = [6, 8]
    s, _ = validate_schema(spec_dict)
    assert check_midi(good_midi(s), s) == []
    mid = load(good_midi(s))
    mid.tracks[0][1].denominator = 4
    assert "signature_mismatch" in codes(check_midi(save(mid), s))


def test_check_never_raises_on_random_corruption(spec):
    import random

    rng = random.Random(0)
    base = good_midi(spec)
    seen_error = 0
    for _ in range(300):
        data = bytearray(base)
        for _ in range(rng.randint(1, 6)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[: rng.randrange(len(data))]
        issues = check_midi(bytes(data), spec)  # must not raise
        seen_error += bool(issues)
    assert seen_error > 100, "random corruption was mostly not detected"


def test_unused_import_guard():
    assert effective_seed  # keeps the import meaningful for static checkers
