"""RuleProvider: keyword/dictionary translation of a prompt into a MusicSpec. No network, no keys.

This is stage 3 of the haru roadmap (docs/음악생성분석/03 §3): a sentence -> {tempo, key, mood, instruments}
rule table. It is deliberately simple and deterministic: the same prompt always gives the same dict.
Everything it decides can be inspected with `RuleProvider.explain(prompt)`.
"""
from __future__ import annotations

import re
from typing import Any

from ..spec import MODE_INTERVALS, PITCH_CLASS, TIME_SIGNATURES

# ----- dictionaries (English + a few Korean words) -----

# mood id -> (trigger words, energy 0..1, preferred mode, default bpm, motif key)
MOODS: dict[str, tuple[tuple[str, ...], float, str, int, str]] = {
    "calm": (("calm", "peace", "serene", "serenity", "gentle", "soft", "잔잔", "평화", "고요"), 0.25, "major", 72, "arch_slow"),
    "relaxing": (("relax", "relaxing", "laid-back", "laid back", "chill", "lo-fi", "lofi", "편안", "여유"), 0.3, "dorian", 85, "arch_slow"),
    "sad": (("sad", "melancholic", "melancholy", "sorrow", "grief", "lonely", "슬픈", "슬픔", "우울"), 0.3, "minor", 62, "fall"),
    "dark": (("dark", "mysterious", "tension", "ominous", "eerie", "어두운", "긴장", "미스터리"), 0.5, "phrygian", 80, "fall"),
    "happy": (("happy", "cheerful", "upbeat", "joy", "joyful", "bright", "행복", "신나는", "밝은"), 0.7, "major", 120, "bounce"),
    "playful": (("playful", "bouncy", "fun", "quirky", "경쾌", "장난"), 0.7, "mixolydian", 128, "bounce"),
    "energetic": (("energetic", "powerful", "intense", "driving", "aggressive", "에너지", "강렬", "격렬"), 0.9, "minor", 140, "drive"),
    "epic": (("epic", "heroic", "triumphant", "grand", "cinematic", "웅장", "영웅"), 0.8, "major", 100, "rise"),
    "romantic": (("romantic", "tender", "love", "warmth", "warm", "로맨틱", "사랑", "따뜻"), 0.4, "major", 90, "arch_slow"),
    "meditative": (("meditative", "meditation", "ambient", "zen", "명상", "앰비언트"), 0.15, "lydian", 58, "arch_slow"),
}
MOOD_ORDER = tuple(MOODS)  # tie-break order

# instrument -> trigger words; order inside a list decides who becomes the lead
INSTRUMENT_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("electric_piano", ("electric piano", "rhodes", "일렉트릭 피아노")),
    ("piano", ("piano", "피아노")),
    ("flute", ("flute", "플루트", "피리")),
    ("violin", ("violin", "바이올린")),
    ("trumpet", ("trumpet", "brass", "트럼펫")),
    ("strings", ("string ensemble", "strings", "현악")),
    ("sax", ("saxophone", "sax", "색소폰")),
    ("clarinet", ("clarinet", "클라리넷")),
    ("marimba", ("marimba", "xylophone", "마림바")),
    ("synth_lead", ("synth lead", "lead synth", "신스 리드")),
    ("steel_guitar", ("electric guitar", "steel guitar", "일렉기타", "일렉트릭 기타")),
    ("nylon_guitar", ("acoustic guitar", "nylon guitar", "guitar", "harp", "기타", "하프")),
    ("organ", ("organ", "오르간")),
]
LEAD_INSTRUMENTS = ("piano", "electric_piano", "flute", "violin", "strings", "trumpet", "sax", "clarinet", "marimba", "synth_lead", "steel_guitar", "nylon_guitar")
BASS_WORDS = (("upright_bass", ("upright bass", "double bass", "contrabass", "더블베이스")),
              ("synth_bass", ("synth bass", "신스 베이스")),
              ("electric_bass", ("electric bass", "bass guitar", "bass", "베이스")))
PAD_WORDS = ("pad", "pads", "ambient", "ensemble", "orchestral", "orchestra", "strings", "string ensemble", "패드", "오케스트라", "현악")
COMP_INSTRUMENTS = ("piano", "electric_piano", "organ", "nylon_guitar", "steel_guitar", "violin", "strings", "marimba")
DRUM_WORDS = ("drum", "drums", "percussion", "timpani", "beat", "드럼", "타악")

# drum style words
STYLE_WORDS = [
    ("bossa", ("bossa", "samba", "보사노바")),
    ("four_on_floor", ("dance", "electronic", "edm", "house", "techno", "disco", "댄스", "일렉트로닉")),
    ("shuffle", ("jazz", "swing", "blues", "shuffle", "재즈", "스윙")),
    ("half_time", ("ballad", "ambient", "slow", "cinematic", "발라드")),
    ("rock_basic", ("rock", "pop", "punk", "metal", "록", "팝")),
]

TEMPO_WORDS = [
    (("very slow", "largo", "아주 느린"), 55), (("slow", "adagio", "느린", "느리게"), 66),
    (("laid-back", "laid back", "relaxed pace"), 85), (("moderate", "andante", "moderato", "보통"), 96),
    (("lively", "allegro", "brisk"), 120), (("fast", "quick", "빠른", "빠르게"), 140), (("very fast", "presto", "아주 빠른"), 170),
]

MODE_WORDS = tuple(MODE_INTERVALS)  # major minor dorian mixolydian lydian phrygian harmonic_minor

# diatonic progressions (scale degrees) per mode, section A and section B
PROGRESSIONS: dict[str, tuple[list[int], list[int]]] = {
    "major": ([1, 5, 6, 4], [4, 1, 5, 1]),
    "minor": ([1, 6, 3, 7], [4, 1, 5, 1]),
    "dorian": ([1, 4, 1, 7], [4, 1, 7, 1]),
    "mixolydian": ([1, 7, 4, 1], [4, 1, 7, 1]),
    "lydian": ([1, 2, 1, 5], [1, 2, 5, 1]),
    "phrygian": ([1, 2, 1, 7], [1, 7, 6, 1]),
    "harmonic_minor": ([1, 4, 5, 1], [6, 4, 5, 1]),
}

MOTIFS: dict[str, tuple[list[int], list[str]]] = {
    "arch_slow": ([1, 3, 5, 3, 2], ["h", "q", "q", "h", "h"]),
    "fall": ([5, 4, 3, 2, 1], ["q", "q", "q", "q", "h"]),
    "bounce": ([1, 3, 5, 3, 6, 5], ["e", "e", "q", "e", "e", "q"]),
    "drive": ([1, 1, 3, 5, 3, 1], ["e", "e", "e", "e", "q", "q"]),
    "rise": ([1, 5, 8, 5, 6, 8], ["q", "q", "h", "q", "q", "h"]),
}

DEFAULT_TONIC = {"major": "C", "minor": "A", "dorian": "D", "mixolydian": "G", "lydian": "F", "phrygian": "E", "harmonic_minor": "A"}

_KEY_RE = re.compile(r"\b([A-G])(?:[- ]?(sharp|flat)|([#b]))?[- ]?(major|minor|dorian|mixolydian|lydian|phrygian|harmonic minor)\b")
_SIG_RE = re.compile(r"\b([2-7])\s*/\s*([48])\b")
_BPM_RE = re.compile(r"(\d{2,3})\s*(?:bpm|BPM|Bpm|비피엠)")

MAX_PROMPT_CHARS = 2000


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def _triad(mode: str, tonic_pc: int, degree: int) -> str:
    iv = MODE_INTERVALS[mode]
    root = iv[degree - 1]
    third = (iv[(degree + 1) % 7] - root) % 12
    fifth = (iv[(degree + 3) % 7] - root) % 12
    pc = (tonic_pc + root) % 12
    name = NAMES[pc]
    if third == 3 and fifth == 7:
        return name + "m"
    if third == 3 and fifth == 6:
        return name + "dim"
    if third == 4 and fifth == 8:
        return name + "aug"
    return name


NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


class RuleProvider:
    """Prompt text -> spec dict by dictionary rules. Name: 'rule'."""

    name = "rule"

    def generate_spec(self, prompt: str, schema: dict[str, Any] | None = None, *, feedback=None, seed: int | None = None) -> dict[str, Any]:
        spec, _ = self._translate(prompt)
        if seed is not None:
            spec["seed"] = seed
        return spec

    def explain(self, prompt: str) -> dict[str, Any]:
        """What the rules decided and why (matched words, defaults used)."""
        return self._translate(prompt)[1]

    # ------------------------------------------------------------------
    def _translate(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        text = str(prompt)[:MAX_PROMPT_CHARS]
        low = text.lower()
        why: dict[str, Any] = {"defaults": []}

        # mood: the one with most trigger words, ties by MOOD_ORDER
        scores = {m: sum(low.count(w) for w in MOODS[m][0]) for m in MOODS}
        best = max(MOOD_ORDER, key=lambda m: (scores[m], -MOOD_ORDER.index(m)))
        mood = best if scores[best] > 0 else "calm"
        if scores[best] == 0:
            why["defaults"].append("mood=calm (no mood word)")
        why["mood"] = mood
        _words, energy, mode_pref, bpm_default, motif_key = MOODS[mood]

        # key
        tonic, mode = None, None
        m = _KEY_RE.search(text)
        if m:
            letter, word, sym, mword = m.groups()
            acc = "#" if (word == "sharp" or sym == "#") else "b" if (word == "flat" or sym == "b") else ""
            cand = letter + acc
            if cand in PITCH_CLASS:
                tonic, mode = cand, mword.replace(" ", "_")
        if tonic is None:
            # a mode word alone ("a dorian feel") keeps the default tonic of that mode
            mw = next((w for w in MODE_WORDS if w.replace("_", " ") in low), None)
            mode = mw or mode_pref
            tonic = DEFAULT_TONIC[mode]
            why["defaults"].append(f"key={tonic} {mode} ({'mode word' if mw else 'from mood'})")
        why["key"] = f"{tonic} {mode}"

        # time signature
        sig = [4, 4]
        ms = _SIG_RE.search(text)
        if ms and (int(ms.group(1)), int(ms.group(2))) in TIME_SIGNATURES:
            sig = [int(ms.group(1)), int(ms.group(2))]
        else:
            why["defaults"].append("time_signature=4/4")
        why["time_signature"] = sig

        # tempo
        mb = _BPM_RE.search(text)
        if mb and 40 <= int(mb.group(1)) <= 220:
            bpm = int(mb.group(1))
        else:
            bpm = next((b for words, b in sorted(TEMPO_WORDS, key=lambda t: -max(len(w) for w in t[0])) if _has(low, words)), None)
            if bpm is None:
                bpm = bpm_default
                why["defaults"].append(f"tempo={bpm} (from mood)")
        why["tempo_bpm"] = bpm

        # instruments
        found = [(low.find(w), inst) for inst, words in INSTRUMENT_WORDS for w in words if w in low]
        order: list[str] = []
        for _pos, inst in sorted(found):
            if inst not in order:
                order.append(inst)
        # "electric piano" also contains "piano": drop plain piano if only matched inside it
        if "electric_piano" in order and "piano" in order and low.count("piano") <= low.count("electric piano") + low.count("일렉트릭 피아노"):
            order.remove("piano")
        # "electric guitar"/"acoustic guitar" also contain "guitar"
        if "steel_guitar" in order and "nylon_guitar" in order and low.count("guitar") <= low.count("electric guitar") + low.count("steel guitar"):
            order.remove("nylon_guitar")
        leads = [i for i in order if i in LEAD_INSTRUMENTS]
        lead = leads[0] if leads else "piano"
        if not leads:
            why["defaults"].append("lead=piano")
        why["lead"] = lead

        bass = next((inst for inst, words in BASS_WORDS if _has(low, words)), None)
        has_drums = _has(low, DRUM_WORDS) or energy >= 0.7
        want_pad = _has(low, PAD_WORDS) or mood in ("meditative", "epic")
        comp = next((i for i in order if i != lead and i in COMP_INSTRUMENTS), None)
        if want_pad and comp == "strings":
            comp = None  # the strings are the pad
        if bass is None and (has_drums or energy >= 0.4):
            bass = "electric_bass"
        style = next((s for s, words in STYLE_WORDS if _has(low, words)), None)
        if style is None:
            style = "half_time" if energy < 0.35 else "rock_basic"
            why["defaults"].append(f"drum style={style} (from energy)")
        why["bass"], why["drums"] = bass, style if has_drums else None

        # sections: A (4 bars) and B (4 bars), then A again
        prog_a, prog_b = PROGRESSIONS[mode]
        tpc = PITCH_CLASS[tonic]
        chords_a = [_triad(mode, tpc, d) for d in prog_a]
        chords_b = [_triad(mode, tpc, d) for d in prog_b]
        sections = [
            {"name": "A", "bars": 4, "chords": chords_a, "energy": round(energy, 2)},
            {"name": "B", "bars": 4, "chords": chords_b, "energy": round(min(1.0, energy + 0.2), 2)},
            {"name": "A2", "bars": 4, "chords": chords_a, "energy": round(energy, 2)},
        ]
        degrees, rhythm = MOTIFS[motif_key]
        tracks: list[dict[str, Any]] = [
            {"role": "melody", "instrument": lead, "motif": {"degrees": list(degrees), "rhythm": list(rhythm)}},
        ]
        if comp or (not want_pad and energy < 0.7):
            tracks.append({"role": "chords", "instrument": comp or ("piano" if lead != "piano" else "electric_piano")})
        if want_pad:
            tracks.append({"role": "pad", "instrument": "strings" if _has(low, ("strings", "string", "orchestr", "현악")) else "synth_pad"})
        if bass:
            tracks.append({"role": "bass", "instrument": bass})
        if has_drums:
            tracks.append({"role": "drums", "style": style})

        words = re.sub(r"[^A-Za-z0-9가-힣 #-]", " ", text).split()
        title = (mood.capitalize() + " " + tonic + " " + mode.replace("_", " "))[:60]
        spec = {
            "version": "1", "title": title, "tempo_bpm": bpm, "time_signature": sig,
            "key": {"tonic": tonic, "mode": mode}, "sections": sections, "tracks": tracks,
        }
        why["words_seen"] = len(words)
        return spec, why
