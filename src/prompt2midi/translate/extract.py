"""Sentence -> Intent v1. Extraction only: a value that is not stated stays null / empty, nothing is composed or defaulted here.

Rules (same words and priorities as the old RuleProvider, moved here):
  mood         the single mood with the most word hits (ties: lexicon order); other moods that also matched go to raw.mood
  genre        every genre whose word occurs, in lexicon (priority) order; hiphop words are kept in raw.genre only
  instruments  named instruments in text order, then synth_pad, bass, drums
  tempo_bpm    "N BPM" (40..200; 201..220 is clamped to 200 with a conflict), else a tempo word normalised to a number
  key          the first explicit key (English or Korean), else a mode word alone; several different keys -> conflict
  time_signature  3/4, 6/8, 4분의 3박자, 3박자 ... (only the six agreed values; others stay in raw)
Normalisation always keeps the original wording in raw.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from ..intent import Intent
from .keys import _BPM_RE, find_keys, find_mode_word_span, find_time_signature

MAX_PROMPT_CHARS = 2000
INTENT_TEMPO_MIN, INTENT_TEMPO_MAX = 40, 200
LEGACY_BPM_MAX = 220  # the old rule accepted 40..220; Intent v1 allows 40..200
RAW_MAX = 200
MAX_INSTRUMENTS = 8


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, Any]:
    """All lexicon files as {name: parsed JSON}. Files: translate/lexicon/{moods,instruments,genres,tempo}.json."""
    base = resources.files("prompt2midi.translate").joinpath("lexicon")
    return {n: json.loads(base.joinpath(f"{n}.json").read_text(encoding="utf-8")) for n in ("moods", "instruments", "genres", "tempo")}


@dataclass
class Extraction:
    intent: Intent
    trace: dict[str, Any] = field(default_factory=dict)  # what matched, for RuleProvider.explain


def _cut(text: str) -> str:
    return text if len(text) <= RAW_MAX else text[: RAW_MAX - 1] + "…"


def _has(low: str, words) -> bool:
    return any(w in low for w in words)


_FRACTION_RE = re.compile(r"(?<![0-9/])(\d{1,2})\s*/\s*(\d{1,2})(?![0-9/])")
_KO_FRACTION_RE = re.compile(r"\d{1,2}\s*분의\s*\d{1,2}\s*박자?")


def translate(prompt: str) -> Extraction:
    text = str(prompt)[:MAX_PROMPT_CHARS]
    low = text.lower()
    lex = load_lexicon()
    trace: dict[str, Any] = {}
    data: dict[str, Any] = {}
    raw: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []

    # ---- mood: the one with the most hits ----
    moods = lex["moods"]["moods"]
    order = list(moods)
    hits = {m: [w for w in words if w in low] for m, words in moods.items()}
    scores = {m: sum(low.count(w) for w in moods[m]) for m in moods}
    best = max(order, key=lambda m: (scores[m], -order.index(m)))
    trace["mood_scores"] = {m: scores[m] for m in order if scores[m]}
    if scores[best] > 0:
        data["mood"] = [best]
        others = [f"{m} ({', '.join(hits[m])})" for m in order if m != best and scores[m]]
        raw["mood"] = _cut(", ".join(hits[best]) + (f"; also: {'; '.join(others)}" if others else ""))
        trace["mood"] = best

    # ---- genre (priority order of the lexicon) ----
    genres = lex["genres"]["genres"]
    matched = [(g["id"], [w for w in g["words"] if w in low]) for g in genres]
    matched = [(i, ws) for i, ws in matched if ws]
    unsupported = [(u["id"], [w for w in u["words"] if w in low]) for u in lex["genres"]["unsupported"]]
    unsupported = [(i, ws) for i, ws in unsupported if ws]
    if matched:
        data["genre"] = [i for i, _ in matched][:3]
    bits = [f"{i}: {', '.join(ws)}" for i, ws in matched] + [f"{i} (not in the genre enum): {', '.join(ws)}" for i, ws in unsupported]
    if bits:
        raw["genre"] = _cut("; ".join(bits))
    trace["genre_words"] = {i: ws for i, ws in matched}
    trace["unsupported_genres"] = {i: ws for i, ws in unsupported}

    # ---- key ----
    keys = find_keys(text)
    if keys:
        _pos, tonic, mode, wording = keys[0]
        data["tonic"], data["mode"] = tonic, mode
        raw["tonic"] = _cut(wording)
        trace["key"] = {"source": "explicit key", "wording": wording}
        distinct = list(dict.fromkeys((k[1], k[2]) for k in keys))
        if len(distinct) > 1:
            conflicts.append({"fields": ["tonic", "mode"], "reason": _cut("several keys are named (" + ", ".join(f"{t} {m}" for t, m in distinct) + "); the first one was used")})
    else:
        mw = find_mode_word_span(low, text)
        if mw:
            data["mode"] = mw[0]
            raw["mode"] = _cut(mw[1])
            trace["key"] = {"source": "mode word", "wording": mw[1]}

    # ---- time signature ----
    sig = find_time_signature(text)
    if sig:
        data["time_signature"] = f"{sig[0]}/{sig[1]}"
        trace["time_signature"] = data["time_signature"]
    else:
        m = _KO_FRACTION_RE.search(text) or next((x for x in _FRACTION_RE.finditer(text)), None)
        if m:
            raw["time_signature"] = _cut(m.group(0))  # a meter-like phrase outside the six agreed values: kept, not guessed
            trace["time_signature_unsupported"] = m.group(0)

    # ---- tempo ----
    word_bpm, word_hit = None, None
    for group in sorted(lex["tempo"]["words"], key=lambda g: -max(len(w) for w in g["words"])):
        found = [w for w in group["words"] if w in low]
        if found:
            word_bpm, word_hit = group["bpm"], found[0]
            break
    mb = _BPM_RE.search(text)
    bpm_given = int(mb.group(1)) if mb else None
    if bpm_given is not None and INTENT_TEMPO_MIN <= bpm_given <= LEGACY_BPM_MAX:
        if bpm_given > INTENT_TEMPO_MAX:
            data["tempo_bpm"] = INTENT_TEMPO_MAX
            raw["tempo_bpm"] = _cut(mb.group(0))
            conflicts.append({"fields": ["tempo_bpm"], "reason": f"{bpm_given} bpm is above the Intent range ({INTENT_TEMPO_MIN}-{INTENT_TEMPO_MAX}); clamped to {INTENT_TEMPO_MAX}"})
        else:
            data["tempo_bpm"] = bpm_given
        trace["tempo"] = {"source": "bpm", "wording": mb.group(0)}
        if word_bpm is not None and abs(word_bpm - data["tempo_bpm"]) > 0.25 * max(word_bpm, data["tempo_bpm"]):
            conflicts.append({"fields": ["tempo_bpm"], "reason": _cut(f"'{word_hit}' suggests about {word_bpm} bpm but {data['tempo_bpm']} bpm is stated; the number was used")})
    else:
        if mb:
            raw["tempo_bpm"] = _cut(mb.group(0))  # out of range (< 40 or > 220): ignored like the old rule
            trace["tempo_ignored"] = mb.group(0)
        if word_bpm is not None:
            data["tempo_bpm"] = word_bpm
            raw["tempo_bpm"] = _cut(word_hit if "tempo_bpm" not in raw else raw["tempo_bpm"] + "; " + word_hit)
            trace["tempo"] = {"source": "tempo word", "wording": word_hit}

    # ---- instruments ----
    ins = lex["instruments"]
    found = [(low.find(w), spec["id"]) for spec in ins["instruments"] for w in spec["words"] if w in low]
    ordered: list[str] = []
    for _pos, name in sorted(found):
        if name not in ordered:
            ordered.append(name)
    if "electric_piano" in ordered and "piano" in ordered and low.count("piano") <= low.count("electric piano") + low.count("일렉트릭 피아노"):
        ordered.remove("piano")  # "electric piano" also contains "piano"
    if "steel_guitar" in ordered and "nylon_guitar" in ordered and low.count("guitar") <= low.count("electric guitar") + low.count("steel guitar"):
        ordered.remove("nylon_guitar")  # "electric guitar" also contains "guitar"
    names = list(ordered)  # text order; which one leads / plays chords is the expander's decision
    if _has(low, ins["pad"]) and "synth_pad" not in names:
        names.append("synth_pad")
    bass = next((g["id"] for g in ins["bass"] if _has(low, g["words"])), None)
    if bass:
        names.append(bass)
    if _has(low, ins["drums"]):
        names.append("drums")
    if names:
        data["instruments"] = names[:MAX_INSTRUMENTS]
        if len(names) > MAX_INSTRUMENTS:
            raw["instruments"] = _cut("more than 8 instruments named; dropped: " + ", ".join(names[MAX_INSTRUMENTS:]))
    trace["instruments"] = names

    data["conflicts"] = conflicts
    data["raw"] = raw
    return Extraction(Intent.model_validate(data), trace)
