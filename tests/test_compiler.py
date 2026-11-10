import copy
import hashlib
import itertools
import os
import subprocess
import sys

import mido
import pytest

from prompt2midi import spec as S
from prompt2midi.compiler import PPQ, CompileError, compile_spec
from prompt2midi.music import effective_register, motif_pitches
from prompt2midi.validate import check_midi, validate_schema, validate_spec
from prompt2midi.validate.theory import CHORD_TONES, scale_pitch_classes
from tests.conftest import VALID_SPEC


def make(d):
    spec, issues = validate_schema(d)
    assert not issues, issues
    return spec


def notes_by_channel(data: bytes):
    """{channel: [(start, end, pitch, velocity)]} from the compiled bytes."""
    out: dict[int, list] = {}
    for tr in mido.MidiFile(file=__import__("io").BytesIO(data)).tracks:
        t, open_ = 0, {}
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                open_[(m.channel, m.note)] = (t, m.velocity)
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                start, vel = open_.pop((m.channel, m.note))
                out.setdefault(m.channel, []).append((start, t, m.note, vel))
    return {c: sorted(v) for c, v in out.items()}


def clean(data, spec):
    issues = check_midi(data, spec)
    assert issues == [], [i.as_dict() for i in issues]


# ---------- determinism ----------

def test_same_spec_same_bytes(spec_dict):
    spec = make(spec_dict)
    assert compile_spec(spec) == compile_spec(spec) == compile_spec(make(spec_dict))


def test_same_bytes_in_a_fresh_process_with_other_hash_seed(spec_dict, tmp_path):
    here = compile_spec(make(spec_dict))
    code = (
        "import json,sys; from prompt2midi.spec import MusicSpec; from prompt2midi.compiler import compile_spec;"
        "sys.stdout.buffer.write(compile_spec(MusicSpec.model_validate(json.loads(sys.argv[1]))))"
    )
    import json
    for hs in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        out = subprocess.run([sys.executable, "-c", code, json.dumps(spec_dict)], capture_output=True, env=env, check=True).stdout
        assert out == here, f"bytes differ with PYTHONHASHSEED={hs}"


def test_different_seeds_give_different_bytes(spec_dict):
    spec = make(spec_dict)
    digests = {hashlib.sha256(compile_spec(spec, seed=s)).hexdigest() for s in range(1, 31)}
    assert len(digests) == 30


def test_seed_argument_equals_seed_field(spec_dict):
    a = compile_spec(make(spec_dict), seed=777)
    spec_dict["seed"] = 777
    assert a == compile_spec(make(spec_dict))


def test_derived_seed_when_missing(spec_dict):
    del spec_dict["seed"]
    a = compile_spec(make(spec_dict))
    assert a == compile_spec(make(spec_dict))
    spec_dict["tempo_bpm"] += 1
    assert a != compile_spec(make(spec_dict))


def test_seed_changes_notes_but_not_structure(spec_dict):
    spec = make(spec_dict)
    a, b = compile_spec(spec, seed=1), compile_spec(spec, seed=2)
    assert a != b
    clean(a, spec), clean(b, spec)
    na, nb = notes_by_channel(a), notes_by_channel(b)
    assert set(na) == set(nb) == {0, 1, 9}  # same instruments/roles


def test_golden_digest_of_the_reference_spec():
    """Pins the exact output for the reference spec. If you change the compiler on purpose,
    update this digest in the same commit and say so in the PR."""
    data = compile_spec(make(copy.deepcopy(VALID_SPEC)))
    assert hashlib.sha256(data).hexdigest() == GOLDEN


GOLDEN = "66bd5312b3d456eafad4b3a62efa31a348d7965d8eefcb5ec9005194b352c385"


# ---------- every compile result passes MidiCheck ----------

ALL_SIGS = list(S.TIME_SIGNATURES)
FULL_TRACKS = [
    {"role": "melody", "instrument": "piano", "motif": {"degrees": [1, 3, 5, 8, 5, 3], "rhythm": ["q", "e", "e", "q", "q", "h"]}},
    {"role": "bass", "instrument": "electric_bass"},
    {"role": "chords", "instrument": "organ"},
    {"role": "pad", "instrument": "synth_pad"},
    {"role": "drums", "style": "rock_basic"},
]


def spec_with(**kw):
    d = copy.deepcopy(VALID_SPEC)
    d.pop("seed", None)
    d["tracks"] = copy.deepcopy(FULL_TRACKS)
    for k, v in kw.items():
        d[k] = v
    return d


@pytest.mark.parametrize("sig", ALL_SIGS, ids=lambda s: f"{s[0]}-{s[1]}")
@pytest.mark.parametrize("style", [d.value for d in S.DrumStyle])
def test_every_time_signature_and_drum_style(sig, style):
    d = spec_with(time_signature=list(sig))
    d["tracks"][-1]["style"] = style
    spec = make(d)
    data = compile_spec(spec)
    clean(data, spec)
    drums = notes_by_channel(data)[9]
    assert drums and all(0 <= n[2] <= 127 for n in drums)
    assert any(n[0] == 0 and n[2] == 36 for n in drums), "kick on the first downbeat"


@pytest.mark.parametrize("mode", [m.value for m in S.Mode])
@pytest.mark.parametrize("tonic", [t.value for t in S.Tonic])
def test_every_mode_and_tonic(tonic, mode):
    d = spec_with(key={"tonic": tonic, "mode": mode})
    d["sections"] = [{"name": "A", "bars": 4, "chords": [tonic, tonic + "m", tonic], "energy": 0.6},
                     {"name": "B", "bars": 2, "chords": [tonic + "7"], "energy": 0.2}]
    spec = make(d)
    data = compile_spec(spec)
    clean(data, spec)
    scale = scale_pitch_classes(spec)
    melody = notes_by_channel(data)[0]
    assert melody and all(n[2] % 12 in scale for n in melody), "melody leaves the key"
    lo, hi = effective_register(spec.tracks[0])
    assert all(lo <= n[2] <= hi for n in melody), "melody leaves the register"


@pytest.mark.parametrize("instrument", [i.value for i in S.Instrument])
def test_every_instrument_as_melody_with_default_register(instrument):
    d = spec_with()
    d["tracks"][0]["instrument"] = instrument
    spec = make(d)
    ra, rb = effective_register(spec.tracks[0])
    if rb - ra < 12:  # bass instruments: the default melody register (60..84) is cut to 60..67
        _, report = validate_spec(d)
        assert any(i.code == "register_too_narrow" for i in report.errors)
        with pytest.raises(CompileError):
            compile_spec(spec)
        return
    data = compile_spec(spec)
    clean(data, spec)
    lo, hi = S.INSTRUMENT_RANGE[instrument]
    assert all(lo <= n[2] <= hi for n in notes_by_channel(data)[0])
    prog = [m.program for tr in mido.MidiFile(file=__import__("io").BytesIO(data)).tracks for m in tr if m.type == "program_change" and m.channel == 0]
    assert prog == [S.GM_PROGRAM[instrument]]


@pytest.mark.parametrize("energy", [0.0, 0.34, 0.35, 0.5, 1.0])
@pytest.mark.parametrize("cpb", [1, 2])
def test_energy_and_harmonic_rhythm(energy, cpb):
    d = spec_with()
    d["sections"] = [{"name": "A", "bars": 3, "chords": ["Cm", "Ab", "Eb"], "chords_per_bar": cpb, "energy": energy},
                     {"name": "B", "bars": 2, "chords": ["Fm"], "chords_per_bar": cpb, "energy": energy}]
    spec = make(d)
    clean(compile_spec(spec), spec)


def test_single_bar_single_section_and_longest_allowed():
    d = spec_with()
    d["sections"] = [{"name": "A", "bars": 1, "chords": ["Cm"]}]
    spec = make(d)
    clean(compile_spec(spec), spec)
    d["tempo_bpm"] = 60
    d["sections"] = [{"name": "A", "bars": 32, "chords": ["Cm", "Ab"]}, {"name": "B", "bars": 32, "chords": ["Fm"]}, {"name": "C", "bars": 11, "chords": ["Cm"]}]
    spec = make(d)  # 75 bars at 60 bpm = exactly 300 s
    clean(compile_spec(spec), spec)


def test_many_random_specs_all_pass_midicheck():
    import random

    rng = random.Random(2024)
    tonics, modes, styles = [t.value for t in S.Tonic], [m.value for m in S.Mode], [d.value for d in S.DrumStyle]
    for n in range(150):
        d = spec_with(tempo_bpm=rng.randint(40, 220), time_signature=list(rng.choice(ALL_SIGS)),
                      key={"tonic": rng.choice(tonics), "mode": rng.choice(modes)}, seed=rng.randrange(2**32))
        d["sections"] = [{"name": f"S{i}", "bars": rng.randint(1, 6), "chords": [rng.choice(tonics) + rng.choice(["", "m", "7", "m7", "maj7", "dim", "sus4"]) for _ in range(rng.randint(1, 5))],
                          "chords_per_bar": rng.choice([1, 2]), "energy": round(rng.random(), 2)} for i in range(rng.randint(1, 5))]
        rhythm = rng.choice([["q", "q", "q", "q"], ["e", "rq", "e", "h"], ["s", "s", "e", "q."]])
        n_notes = sum(1 for r in rhythm if not r.startswith("r"))
        d["tracks"][0]["motif"] = {"degrees": [rng.choice([1, 2, 3, 5, 6, 8]) for _ in range(n_notes)], "rhythm": rhythm}
        d["tracks"][-1]["style"] = rng.choice(styles)
        spec, report = validate_spec(d)
        assert spec is not None and report.ok, (d, report.issues)
        data = compile_spec(spec)
        issues = check_midi(data, spec)
        assert issues == [], (n, [i.as_dict() for i in issues])


# ---------- musical content ----------

def test_melody_rhythm_is_exact():
    d = spec_with()
    d["tracks"][0]["motif"] = {"degrees": [1, 2, 3], "rhythm": ["q", "rq", "e", "e"]}
    d["sections"] = [{"name": "A", "bars": 2, "chords": ["Cm"]}]
    spec = make(d)
    starts = [n[0] for n in notes_by_channel(compile_spec(spec))[0]]
    # one pass = q(0) rq(480) e(960) e(1200), 1440 ticks long; it repeats at 1440 and 2880 and is cut at the end (3840)
    assert starts == [0, 960, 1200, 1440, 2400, 2640, 2880]
    # beat length is PPQ
    assert PPQ == 480


def test_melody_never_runs_past_its_section_and_is_cut_at_the_end():
    d = spec_with()
    d["tracks"][0]["motif"] = {"degrees": [1], "rhythm": ["w"]}
    d["sections"] = [{"name": "A", "bars": 1, "chords": ["Cm"]}, {"name": "B", "bars": 1, "chords": ["Fm"]}]
    d["time_signature"] = [3, 4]  # whole note (4 beats) is longer than a 3/4 bar
    spec = make(d)
    notes = notes_by_channel(compile_spec(spec))[0]
    bar = 3 * PPQ
    assert [(n[0], n[1] <= bar * (i + 1)) for i, n in enumerate(notes)] == [(0, True), (bar, True)]


def test_chord_track_plays_exactly_the_chord_tones():
    d = spec_with()
    d["sections"] = [{"name": "A", "bars": 4, "chords": ["Cm7", "Ab", "Fm", "G7"], "energy": 0.1}]
    spec = make(d)
    chords = notes_by_channel(compile_spec(spec))[2]
    bar = 4 * PPQ
    for i, name in enumerate(["Cm7", "Ab", "Fm", "G7"]):
        root, quality = S.parse_chord(name)
        want = {(root + iv) % 12 for iv in CHORD_TONES[quality]}
        got = {n[2] % 12 for n in chords if n[0] == i * bar}
        assert got == want, name


def test_bass_starts_each_chord_on_its_root():
    d = spec_with()
    d["sections"] = [{"name": "A", "bars": 4, "chords": ["Cm", "Ab", "Eb", "Bb"], "energy": 0.2}]
    spec = make(d)
    bass = notes_by_channel(compile_spec(spec))[1]
    lo, hi = effective_register(spec.tracks[1])
    for i, root_pc in enumerate([0, 8, 3, 10]):
        first = [n for n in bass if n[0] == i * 4 * PPQ]
        assert first and first[0][2] % 12 == root_pc and lo <= first[0][2] <= hi


def test_four_on_floor_has_a_kick_on_every_beat():
    d = spec_with()
    d["tracks"][-1]["style"] = "four_on_floor"
    spec = make(d)
    kicks = [n[0] for n in notes_by_channel(compile_spec(spec))[9] if n[2] == 36]
    assert kicks[:4] == [0, 480, 960, 1440]


def test_higher_energy_means_more_notes():
    def count(e):
        d = spec_with()
        d["sections"] = [{"name": "A", "bars": 4, "chords": ["Cm", "Ab"], "energy": e}]
        data = compile_spec(make(d))
        return {c: len(v) for c, v in notes_by_channel(data).items()}
    low, high = count(0.1), count(0.9)
    assert high[1] > low[1] and high[2] > low[2]


def test_velocities_stay_in_midi_range():
    d = spec_with()
    d["sections"] = [{"name": "A", "bars": 8, "chords": ["Cm"], "energy": 1.0}]
    for seed in range(20):
        data = compile_spec(make(d), seed=seed)
        assert all(1 <= n[3] <= 127 for v in notes_by_channel(data).values() for n in v)


# ---------- refusing bad specs ----------

def test_compile_rejects_a_spec_that_fails_the_theory_check(spec_dict):
    spec_dict["sections"][1]["name"] = "A"
    with pytest.raises(CompileError) as e:
        compile_spec(make(spec_dict))
    assert e.value.issues and e.value.issues[0].code == "duplicate_section"


def test_motif_that_does_not_fit_the_register_is_an_error(spec_dict):
    spec_dict["tracks"][0]["register"] = [60, 72]
    spec_dict["tracks"][0]["motif"] = {"degrees": [1, 14], "rhythm": ["q", "q"]}  # two octaves apart
    spec, report = validate_spec(spec_dict)
    assert not report.ok and any(i.code == "motif_out_of_register" for i in report.errors)
    assert motif_pitches(spec, spec.tracks[0]) is None
    with pytest.raises(CompileError):
        compile_spec(spec)


def test_default_register_narrower_than_an_octave_is_reported(spec_dict):
    spec_dict["tracks"][1] = {"role": "bass", "instrument": "violin"}  # violin 55..103, bass default 36..55
    spec, report = validate_spec(spec_dict)
    assert not report.ok and any(i.code == "register_too_narrow" and i.path == "tracks[1].instrument" for i in report.errors)


def test_non_diatonic_chords_still_compile(spec_dict):
    spec_dict["sections"][0]["chords"] = ["C", "F#7", "B", "Eb"]
    spec = make(spec_dict)
    clean(compile_spec(spec), spec)


def test_non_ascii_title_is_fine(spec_dict):
    spec_dict["title"] = "밤 산책 🌙"
    spec = make(spec_dict)
    data = compile_spec(spec)
    clean(data, spec)
    assert data == compile_spec(spec)


def test_output_is_a_format_1_file_with_expected_tracks(spec_dict):
    data = compile_spec(make(spec_dict))
    mid = mido.MidiFile(file=__import__("io").BytesIO(data))
    assert mid.type == 1 and mid.ticks_per_beat == 480
    assert len(mid.tracks) == 1 + 3  # meta + melody, bass, drums
    assert data[:4] == b"MThd"
