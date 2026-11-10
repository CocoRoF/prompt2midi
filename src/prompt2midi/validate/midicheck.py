"""Step 3 of validation: re-parse generated MIDI bytes and check them against the spec.

This does not trust the compiler: it reads the bytes back (header, chunk layout, events) and
verifies structure, note pairing, ranges, tempo/time-signature meta events and total length.
"""
from __future__ import annotations

import io
import struct
from collections import defaultdict
from fractions import Fraction

import mido

from ..spec import MusicSpec, Role, beats_per_bar, total_bars
from .issues import Issue

DRUM_CHANNEL = 9


def _chunk_layout(data: bytes) -> tuple[list[Issue], int, int, int, list[bytes]]:
    """Check the raw header and chunk lengths. Returns (issues, format, declared_tracks, ppq, track bodies)."""
    if len(data) < 14:
        return [Issue("header", f"file too short ({len(data)} bytes) for a MIDI header", "truncated")], -1, 0, 0, []
    if data[:4] != b"MThd":
        return [Issue("header", "missing 'MThd' header chunk", "bad_header")], -1, 0, 0, []
    length, fmt, ntrks, division = struct.unpack(">IHHH", data[4:14])
    issues: list[Issue] = []
    if length != 6:
        issues.append(Issue("header", f"header length is {length}, expected 6", "bad_header"))
    if fmt not in (0, 1):
        issues.append(Issue("header", f"unsupported MIDI format {fmt} (expected 0 or 1)", "bad_header"))
    if division & 0x8000:
        issues.append(Issue("header", "SMPTE time division is not supported (expected ticks per beat)", "bad_header"))
        division = 0
    elif division == 0:
        issues.append(Issue("header", "ticks per beat is 0", "bad_header"))
    pos = 8 + length
    found = 0
    bodies: list[bytes] = []
    while pos < len(data):
        if pos + 8 > len(data):
            issues.append(Issue("file", f"{len(data) - pos} stray bytes after the last chunk", "trailing_bytes"))
            break
        cid, clen = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if cid != b"MTrk":
            issues.append(Issue("file", f"unexpected chunk {cid!r} at byte {pos}", "bad_chunk"))
            break
        if pos + 8 + clen > len(data):
            issues.append(Issue(f"track[{found}]", f"track chunk declares {clen} bytes but only {len(data) - pos - 8} remain (truncated)", "truncated"))
            found += 1
            break
        bodies.append(data[pos + 8:pos + 8 + clen])
        found += 1
        pos += 8 + clen
    if found != ntrks:
        issues.append(Issue("header", f"header declares {ntrks} tracks but file contains {found}", "track_count"))
    return issues, fmt, ntrks, division, bodies


def _varint(body: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(body):
            raise ValueError("variable-length value runs past the end of the track")
        b = body[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise ValueError("variable-length value longer than 4 bytes")


def _raw_end_of_track(body: bytes) -> tuple[int, bool, str | None]:
    """Walk one track chunk byte by byte (mido stops silently at the first EndOfTrack, so we look ourselves).

    Returns (number of EndOfTrack events, whether the last event is an EndOfTrack, parse error or None).
    """
    pos, status, count, last_is_eot = 0, 0, 0, False
    try:
        while pos < len(body):
            _delta, pos = _varint(body, pos)
            if pos >= len(body):
                raise ValueError("delta time without an event")
            b = body[pos]
            last_is_eot = False
            if b == 0xFF:
                if pos + 1 >= len(body):
                    raise ValueError("meta event without a type")
                mtype = body[pos + 1]
                length, pos = _varint(body, pos + 2)
                if pos + length > len(body):
                    raise ValueError("meta event runs past the end of the track")
                pos += length
                if mtype == 0x2F:
                    count += 1
                    last_is_eot = True
                status = 0
            elif b in (0xF0, 0xF7):
                length, pos = _varint(body, pos + 1)
                if pos + length > len(body):
                    raise ValueError("sysex runs past the end of the track")
                pos += length
                status = 0
            else:
                if b & 0x80:
                    status, pos = b, pos + 1
                elif not status:
                    raise ValueError("data byte without a status byte")
                need = 1 if 0xC0 <= status <= 0xDF else 2
                if pos + need > len(body):
                    raise ValueError("channel event runs past the end of the track")
                if any(x & 0x80 for x in body[pos:pos + need]):
                    raise ValueError("data byte above 127")
                pos += need
    except ValueError as exc:
        return count, last_is_eot, str(exc)
    return count, last_is_eot, None


def check_midi(data: bytes, spec: MusicSpec | None = None) -> list[Issue]:
    """Return the list of problems found in `data` (empty list = valid). Never raises."""
    issues, _fmt, _ntrks, ppq, bodies = _chunk_layout(data)
    if any(i.code in ("truncated", "bad_header") for i in issues):
        return issues
    try:
        mid = mido.MidiFile(file=io.BytesIO(data))
    except Exception as exc:  # mido raises EOFError, ValueError, KeyError... on malformed input
        return issues + [Issue("file", f"cannot parse MIDI: {type(exc).__name__}: {exc}", "unparseable")]
    ppq = mid.ticks_per_beat

    tempos: list[tuple[int, int]] = []
    sigs: list[tuple[int, int, int]] = []
    channels_used: set[int] = set()
    n_notes = 0
    last_end = 0
    last_note_off = 0
    for ti, track in enumerate(mid.tracks):
        tp = f"track[{ti}]"
        t = 0
        open_notes: dict[tuple[int, int], list[int]] = defaultdict(list)
        count, last_is_eot, err = _raw_end_of_track(bodies[ti]) if ti < len(bodies) else (0, False, None)
        if err:
            issues.append(Issue(tp, f"malformed track data: {err}", "malformed_track"))
        elif count == 0:
            issues.append(Issue(tp, "track has no EndOfTrack event", "no_end_of_track"))
        elif count > 1 or not last_is_eot:
            issues.append(Issue(tp, "EndOfTrack must appear exactly once, as the last event", "bad_end_of_track"))
        for m in track:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                open_notes[(m.channel, m.note)].append(t)
                channels_used.add(m.channel)
                n_notes += 1
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                stack = open_notes[(m.channel, m.note)]
                if not stack:
                    issues.append(Issue(tp, f"NoteOff for note {m.note} (ch {m.channel}) at tick {t} has no matching NoteOn", "unmatched_note_off"))
                else:
                    start = stack.pop(0)
                    last_note_off = max(last_note_off, t)
                    if t == start:
                        issues.append(Issue(tp, f"zero-length note {m.note} (ch {m.channel}) at tick {t}", "zero_length_note", "warning"))
            elif m.type == "set_tempo":
                tempos.append((t, m.tempo))
            elif m.type == "time_signature":
                sigs.append((t, m.numerator, m.denominator))
        for (ch, note), starts in sorted(open_notes.items()):
            for s in starts:
                issues.append(Issue(tp, f"note {note} (ch {ch}) started at tick {s} is never turned off", "unterminated_note"))
        last_end = max(last_end, t)

    if n_notes == 0:
        issues.append(Issue("file", "MIDI contains no notes", "no_notes"))

    if spec is None:
        return issues

    want_tempo = mido.bpm2tempo(spec.tempo_bpm)
    if not tempos:
        issues.append(Issue("meta.tempo", "no set_tempo event", "tempo_missing"))
    else:
        if tempos[0][0] != 0:
            issues.append(Issue("meta.tempo", f"first set_tempo is at tick {tempos[0][0]}, expected 0", "tempo_late"))
        for tick, tempo in tempos:
            if tempo != want_tempo:
                issues.append(Issue("meta.tempo", f"tempo {mido.tempo2bpm(tempo):.2f} bpm at tick {tick} != spec {spec.tempo_bpm} bpm", "tempo_mismatch"))
    want_sig = spec.time_signature
    if not sigs:
        issues.append(Issue("meta.time_signature", "no time_signature event", "signature_missing"))
    else:
        if sigs[0][0] != 0:
            issues.append(Issue("meta.time_signature", f"first time_signature is at tick {sigs[0][0]}, expected 0", "signature_late"))
        for tick, n, d in sigs:
            if (n, d) != want_sig:
                issues.append(Issue("meta.time_signature", f"time signature {n}/{d} at tick {tick} != spec {want_sig[0]}/{want_sig[1]}", "signature_mismatch"))

    expected = Fraction(total_bars(spec)) * beats_per_bar(spec) * ppq
    if expected.denominator != 1:
        issues.append(Issue("length", f"expected length {expected} ticks is not a whole number at {ppq} ticks per beat", "length_not_integral"))
    else:
        want = int(expected)
        if last_end != want:
            issues.append(Issue("length", f"MIDI lasts {last_end} ticks but spec implies {want} ticks ({total_bars(spec)} bars)", "length_mismatch"))
        if last_note_off > want:
            issues.append(Issue("length", f"a note ends at tick {last_note_off}, after the piece end ({want})", "note_past_end"))

    has_drums = any(t.role is Role.drums for t in spec.tracks)
    if has_drums and DRUM_CHANNEL not in channels_used:
        issues.append(Issue("channels", "spec has a drums track but channel 10 (index 9) has no notes", "drums_missing"))
    if not has_drums and DRUM_CHANNEL in channels_used:
        issues.append(Issue("channels", "notes on the drum channel (index 9) but the spec has no drums track", "unexpected_drums"))
    return issues
