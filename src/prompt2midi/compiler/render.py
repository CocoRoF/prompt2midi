"""Spec + seed -> MIDI bytes. Deterministic: same spec and seed give identical bytes."""
from __future__ import annotations

import io
from dataclasses import dataclass
from fractions import Fraction

import mido

from ..music import effective_register, motif_pitches
from ..spec import (
    GM_PROGRAM,
    RHYTHM_BEATS,
    MusicSpec,
    Role,
    Track,
    beats_per_bar,
    effective_seed,
    is_rest,
    parse_chord,
    total_bars,
)
from ..validate.theory import CHORD_TONES, check_theory
from . import drums as D
from .rng import Rng

PPQ = 480
CHANNEL = {"melody": 0, "bass": 1, "chords": 2, "pad": 3, "drums": 9}


class CompileError(ValueError):
    """The spec cannot be compiled (it failed validation). `.issues` holds the validator messages."""

    def __init__(self, message: str, issues=()):
        super().__init__(message)
        self.issues = list(issues)


@dataclass
class _Note:
    start: int
    end: int
    pitch: int
    velocity: int


def _ticks(beats: Fraction) -> int:
    t = beats * PPQ
    if t.denominator != 1:
        raise CompileError(f"{beats} beats is not a whole number of ticks at {PPQ} ppq")
    return int(t)


def _vel(base: float, energy: float, rng: Rng, spread: int = 5) -> int:
    return max(1, min(127, int(round(base + 35 * energy)) + rng.jitter(spread)))


def _slots(spec: MusicSpec):
    """Yield (start_tick, length_ticks, chord_name, section_index, section) for every chord slot."""
    bar = _ticks(beats_per_bar(spec))
    pos = 0
    for si, sec in enumerate(spec.sections):
        slot_len = bar * sec.bars // (sec.bars * sec.chords_per_bar)
        n = sec.bars * sec.chords_per_bar
        for i in range(n):
            yield pos + i * slot_len, slot_len, sec.chords[i % len(sec.chords)], si, sec
        pos += bar * sec.bars


def _chord_pitches(name: str, lo: int, hi: int, center: float) -> list[int]:
    root, quality = parse_chord(name)
    out = []
    for iv in CHORD_TONES[quality]:
        pc = (root + iv) % 12
        # pitch with this pitch class inside [lo, hi] nearest to `center`
        best = None
        for p in range(lo, hi + 1):
            if p % 12 == pc and (best is None or abs(p - center) < abs(best - center)):
                best = p
        out.append(best)
    return sorted(out)


def _chords_track(spec: MusicSpec, track: Track, rng: Rng, pad: bool) -> list[_Note]:
    lo, hi = effective_register(track)
    beat = _ticks(Fraction(1))
    notes: list[_Note] = []
    center = (lo + hi) / 2
    for start, length, chord, _si, sec in _slots(spec):
        pitches = _chord_pitches(chord, lo, hi, center)
        center = sum(pitches) / len(pitches)
        sustained = pad or sec.energy < 0.35
        if sustained:
            hits = [(start, length)]
        else:
            hits = [(s, min(beat, start + length - s)) for s in range(start, start + length, beat)]
        for s, ln in hits:
            gate = max(1, int(ln * (0.98 if sustained else 0.85)))
            base = 45 if pad else 62
            for p in pitches:
                notes.append(_Note(s, s + gate, p, _vel(base, sec.energy * (0.4 if pad else 0.8), rng, 4)))
    return notes


def _bass_track(spec: MusicSpec, track: Track, rng: Rng) -> list[_Note]:
    lo, hi = effective_register(track)
    beat = _ticks(Fraction(1))
    notes: list[_Note] = []
    for start, length, chord, _si, sec in _slots(spec):
        root_pc, _q = parse_chord(chord)
        root = lo + (root_pc - lo) % 12
        fifth = root + 7 if root + 7 <= hi else root
        if sec.energy < 0.5:
            plan = [(start, length, root)]
        else:
            plan = []
            for k, s in enumerate(range(start, start + length, beat)):
                ln = min(beat, start + length - s)
                p = fifth if (k % 4 == 2 and rng.random() < 0.6) else root
                plan.append((s, ln, p))
        for s, ln, p in plan:
            notes.append(_Note(s, s + max(1, int(ln * 0.9)), p, _vel(70, sec.energy, rng)))
    return notes


def _melody_track(spec: MusicSpec, track: Track, rng: Rng) -> list[_Note]:
    motif = track.motif
    bar = _ticks(beats_per_bar(spec))
    notes: list[_Note] = []
    pos = 0
    for sec in spec.sections:
        sec_end = pos + bar * sec.bars
        shift = rng.choice([0, 0, 1, -1, 2])
        pitches = motif_pitches(spec, track, shift)
        if pitches is None:  # shifted motif leaves the register: use the unshifted one
            pitches = motif_pitches(spec, track, 0)
        t, ni, ri = pos, 0, 0
        while t < sec_end:
            sym = motif.rhythm[ri % len(motif.rhythm)].value
            ri += 1
            length = min(_ticks(RHYTHM_BEATS[sym]), sec_end - t)
            if not is_rest(sym):
                p = pitches[ni % len(pitches)]
                ni += 1
                notes.append(_Note(t, t + max(1, int(length * 0.92)), p, _vel(65, sec.energy, rng)))
            t += length
        pos = sec_end
    return notes


def _drum_track(spec: MusicSpec, track: Track, rng: Rng) -> list[_Note]:
    sig = spec.time_signature
    steps = D.GRID[sig][0]
    eighth = PPQ // 2
    bar = _ticks(beats_per_bar(spec))
    style = track.style.value
    pattern = D.bar_hits(style, sig)
    notes: list[_Note] = []
    b = 0
    nbars = total_bars(spec)
    for si, sec in enumerate(spec.sections):
        for k in range(sec.bars):
            base = b * bar
            for step, note, vel in pattern:
                off = eighth // 3 if style == "shuffle" and step % 2 == 1 else 0
                t = base + step * eighth + off
                if t >= base + bar:
                    continue
                if note in (D.CLOSED_HAT, D.RIDE) and sec.energy < 0.3 and step % 2 == 1:
                    continue  # thinner texture at low energy
                notes.append(_Note(t, t + 60, note, _vel(vel - 35, sec.energy, rng, 4)))
            last_bar_of_section = k == sec.bars - 1 and b < nbars - 1
            if last_bar_of_section and sec.energy >= 0.4 and steps >= 4:
                for j, step in enumerate(range(steps - 2, steps)):
                    t = base + step * eighth
                    notes.append(_Note(t, t + 60, D.SNARE, min(127, 70 + 15 * j + rng.jitter(3))))
            b += 1
    # two hits on the same drum at the same tick (pattern + fill) collapse into the louder one
    merged: dict[tuple[int, int], _Note] = {}
    for n in notes:
        key = (n.start, n.pitch)
        if key not in merged or n.velocity > merged[key].velocity:
            merged[key] = n
    return list(merged.values())


def _events(notes: list[_Note], channel: int):
    ev = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch, n.end)):
        ev.append((n.start, 1, mido.Message("note_on", channel=channel, note=n.pitch, velocity=n.velocity)))
        ev.append((n.end, 0, mido.Message("note_off", channel=channel, note=n.pitch, velocity=0)))
    return ev


def _to_track(events, total: int, name: str, program: int | None, channel: int) -> mido.MidiTrack:
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=name, time=0))
    if program is not None:
        tr.append(mido.Message("program_change", channel=channel, program=program, time=0))
    last = 0
    for tick, _order, msg in sorted(events, key=lambda e: (e[0], e[1], getattr(e[2], "note", 0))):
        tick = min(tick, total)
        msg = msg.copy(time=tick - last)
        tr.append(msg)
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=total - last))
    return tr


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def compile_spec(spec: MusicSpec, seed: int | None = None) -> bytes:
    """Compile a validated MusicSpec into a Standard MIDI File (format 1, 480 ticks per beat).

    `seed` overrides the spec's own seed (see `effective_seed`). Raises CompileError for specs that
    fail the theory check (warnings, e.g. non-diatonic chords, are allowed).
    """
    errors = [i for i in check_theory(spec) if i.severity == "error"]
    if errors:
        raise CompileError("; ".join(f"{i.path}: {i.message}" for i in errors), errors)
    base_seed = effective_seed(spec) if seed is None else seed & 0xFFFFFFFF
    total = _ticks(Fraction(total_bars(spec)) * beats_per_bar(spec))

    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name=_ascii(spec.title), time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(spec.tempo_bpm), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=spec.time_signature[0], denominator=spec.time_signature[1], time=0))
    meta.append(mido.MetaMessage("end_of_track", time=total))
    mid.tracks.append(meta)

    for track in sorted(spec.tracks, key=lambda t: CHANNEL[t.role.value]):
        role = track.role.value
        rng = Rng(base_seed, role)
        # engine "v2" (docs/compiler-v2.md) delegates to the v1 builders until its PRs land; None/"v1" is v1.
        if track.role is Role.melody:
            notes = _melody_track(spec, track, rng)
        elif track.role is Role.bass:
            notes = _bass_track(spec, track, rng)
        elif track.role is Role.chords:
            notes = _chords_track(spec, track, rng, pad=False)
        elif track.role is Role.pad:
            notes = _chords_track(spec, track, rng, pad=True)
        else:
            notes = _drum_track(spec, track, rng)
        channel = CHANNEL[role]
        program = None if track.role is Role.drums else GM_PROGRAM[track.instrument.value]
        mid.tracks.append(_to_track(_events(notes, channel), total, role, program, channel))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()
