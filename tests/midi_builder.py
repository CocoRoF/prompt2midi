"""Helpers that build (and deliberately break) MIDI bytes without using the compiler."""
from __future__ import annotations

import io
import struct

import mido

from prompt2midi.spec import MusicSpec, beats_per_bar, total_bars

PPQ = 480


def good_midi(spec: MusicSpec, *, drums: bool | None = None) -> bytes:
    """A minimal correct file for `spec`: one tempo/signature track, one note track (+drums)."""
    if drums is None:
        drums = any(t.role.value == "drums" for t in spec.tracks)
    length = int(total_bars(spec) * beats_per_bar(spec) * PPQ)
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(spec.tempo_bpm), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=spec.time_signature[0], denominator=spec.time_signature[1], time=0))
    meta.append(mido.MetaMessage("end_of_track", time=length))
    mid.tracks.append(meta)
    notes = mido.MidiTrack()
    notes.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
    notes.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=PPQ))
    notes.append(mido.MetaMessage("end_of_track", time=length - PPQ))
    mid.tracks.append(notes)
    if drums:
        d = mido.MidiTrack()
        d.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
        d.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=PPQ // 2))
        d.append(mido.MetaMessage("end_of_track", time=length - PPQ // 2))
        mid.tracks.append(d)
    return save(mid)


def save(mid: mido.MidiFile) -> bytes:
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def load(data: bytes) -> mido.MidiFile:
    return mido.MidiFile(file=io.BytesIO(data))


def strip_end_of_track(data: bytes, track_index: int) -> bytes:
    """Remove the final '<delta> FF 2F 00' of one track chunk and fix that chunk's length."""
    out = bytearray(data[:14])
    pos, idx = 14, 0
    while pos < len(data):
        cid, clen = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + clen]
        if idx == track_index:
            assert body.endswith(b"\xff\x2f\x00")
            body = body[:-3]
            body = body[:-1]  # last byte of the delta varint (high bit clear)
            while body and body[-1] & 0x80:  # earlier delta bytes (high bit set)
                body = body[:-1]
        out += cid + struct.pack(">I", len(body)) + body
        pos += 8 + clen
        idx += 1
    return bytes(out)


def insert_raw(data: bytes, track_index: int, at: int, raw: bytes) -> bytes:
    """Insert raw bytes into the body of one track chunk at byte offset `at` and fix the chunk length."""
    out = bytearray(data[:14])
    pos, idx = 14, 0
    while pos < len(data):
        cid, clen = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + clen]
        if idx == track_index:
            body = body[:at] + raw + body[at:]
        out += cid + struct.pack(">I", len(body)) + body
        pos += 8 + clen
        idx += 1
    return bytes(out)
