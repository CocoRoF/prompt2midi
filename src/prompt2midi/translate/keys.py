"""Explicit key / meter / BPM expressions in a sentence (English and Korean). Pure functions, no musical decisions.

Moved from providers/rule.py unchanged (including the false-positive rules of PR #22); `find_key_span` additionally returns the
matched wording for Intent.raw.
"""
from __future__ import annotations

import re

from ..spec import MODE_INTERVALS, PITCH_CLASS, TIME_SIGNATURES

MODE_WORDS = tuple(MODE_INTERVALS)  # major minor dorian mixolydian lydian phrygian harmonic_minor

_KEY_RE = re.compile(r"\b([A-G])(?:[- ]?(sharp|flat)|([#b]))?[- ]?(major|minor|dorian|mixolydian|lydian|phrygian|harmonic minor)\b")
_SIG_RE = re.compile(r"(?<![0-9A-Za-z])([2-7])\s*/\s*([48])(?![0-9A-Za-z])")  # not \b: Korean letters are word chars, so "6/8박자" has no boundary
_BPM_RE = re.compile(r"(\d{2,3})\s*(?:bpm|BPM|Bpm|비피엠)")

# ----- Korean key / meter parsing -----
# Priority for the key (documented in docs/rule-provider.md):
#   1. an explicit key in the prompt (tonic + mode, English "A minor" or Korean "A단조"/"내림나장조"); if several, the first one
#   2. an explicit mode word without a tonic ("도리안", "dorian", bare "단조"/"장조") -> that mode on the mode's default tonic
#   3. otherwise the mode preferred by the mood word, on that mode's default tonic
# An explicit key never changes the mood-driven energy/tempo/motif, only tonic and mode.
KO_LETTERS = {"가": "A", "나": "B", "다": "C", "라": "D", "마": "E", "바": "F", "사": "G"}
KO_MODES = {"장조": "major", "단조": "minor", "믹솔리디안": "mixolydian", "리디안": "lydian", "도리안": "dorian", "프리지안": "phrygian",
            "화성단조": "harmonic_minor", "하모닉마이너": "harmonic_minor", "하모닉 마이너": "harmonic_minor"}
_KO_MODE_ALT = "|".join(sorted((re.escape(k) for k in KO_MODES), key=len, reverse=True))
# What may follow a mode word directly (particles / endings / light nouns). Anything else glued to "장조"/"단조"
# is a longer word ("사장조직", "마장조선시대", "장조림", "단조건") and is NOT a key.
_KO_AFTER = ("에서는|에서도|에서|으로서|으로써|으로는|으로도|으로|로서|로써|로는|로도|로|의|는|은|를|을|가|이|도|와|과|곡|풍|처럼|에|만|까지|부터|보다|"
             "인|이다|입니다|이라서|이라|라서|라|이고|고|며|면|인데|께|스타일|느낌")
_KO_TAIL = r"(?=(?:" + _KO_AFTER + r")*(?![가-힣]))"
_KO_KEY_RE = re.compile(
    r"(?<![가-힣A-Za-z])(?:(올림|내림)\s*)?([가나다라마바사]|[A-Ga-g])(?:\s*(샵|플랫)|([#b]))?(\s*)(" + _KO_MODE_ALT + r")" + _KO_TAIL
)
_BARE_KO_MODE_RE = re.compile(r"(?<![가-힣])(" + _KO_MODE_ALT + r")" + _KO_TAIL)
# a one-syllable note name or a lowercase letter may only be separated from 장조/단조 by a space if 올림/내림 shows it is a key
_SPACE_OK_MODES = {"믹솔리디안", "리디안", "도리안", "프리지안", "화성단조", "하모닉마이너", "하모닉 마이너"}
_KO_NUM = {**{str(i): i for i in range(10)}, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_KO_SIG_RE = re.compile(r"([0-9]|[이삼사오육칠팔구])\s*분의\s*([0-9]|[이삼사오육칠팔구])\s*박자?")
_KO_BEATS_RE = re.compile(r"(?<![0-9/])([2-5]|[이삼사오])\s*박자")
_BEATS_TO_SIG = {2: (2, 4), 3: (3, 4), 4: (4, 4), 5: (5, 4)}


def find_keys(text: str) -> list[tuple[int, str, str, str]]:
    """Every explicit key in the text, in order of appearance: (position, tonic, mode, matched wording)."""
    found: list[tuple[int, str, str, str]] = []
    for m in _KEY_RE.finditer(text):
        letter, word, sym, mword = m.groups()
        acc = "#" if (word == "sharp" or sym == "#") else "b" if (word == "flat" or sym == "b") else ""
        if letter + acc in PITCH_CLASS:
            found.append((m.start(), letter + acc, mword.replace(" ", "_"), m.group(0)))
    for k in _KO_KEY_RE.finditer(text):
        prefix, letter, suffix, sym, gap, mode_ko = k.groups()
        if gap and not prefix and mode_ko not in _SPACE_OK_MODES and not (letter.isascii() and letter.isupper()):
            continue  # "가 장조 …", "a 단조": a particle/article followed by a word, not a key
        letter = KO_LETTERS.get(letter, letter.upper())
        acc = ""
        for mark in (prefix, suffix):
            if mark in ("올림", "샵"):
                acc = "#"
            elif mark in ("내림", "플랫"):
                acc = "b"
        if sym in ("#", "b") and not acc:
            acc = sym
        if letter + acc in PITCH_CLASS:  # e.g. 내림다장조 (Cb) does not exist in our spelling: skipped
            found.append((k.start(), letter + acc, KO_MODES[mode_ko.replace(" ", "")], k.group(0)))
    return sorted(found)


def find_key(text: str) -> tuple[str, str] | None:
    """First explicit key in the text (English or Korean) as (tonic, mode), or None."""
    keys = find_keys(text)
    return (keys[0][1], keys[0][2]) if keys else None


def find_mode_word(low: str, text: str) -> str | None:
    """A mode named without a tonic (English or Korean); None when absent."""
    mw = next((w for w in MODE_WORDS if w.replace("_", " ") in low), None)
    if mw:
        return mw
    bare = _BARE_KO_MODE_RE.search(text)
    return KO_MODES[bare.group(1)] if bare else None


def find_mode_word_span(low: str, text: str) -> tuple[str, str] | None:
    """Like find_mode_word but also returns the matched wording."""
    for w in MODE_WORDS:
        word = w.replace("_", " ")
        if word in low:
            return w, word
    bare = _BARE_KO_MODE_RE.search(text)
    return (KO_MODES[bare.group(1)], bare.group(1)) if bare else None


def find_time_signature(text: str) -> tuple[int, int] | None:
    """Explicit meter: 3/4, 6/8, 4분의 3박자 (= 3/4), 사분의 삼박자, 3박자 (= 3/4). Only values in TIME_SIGNATURES count."""
    cands: list[tuple[int, tuple[int, int]]] = []
    m = _SIG_RE.search(text)
    if m:
        cands.append((m.start(), (int(m.group(1)), int(m.group(2)))))
    k = _KO_SIG_RE.search(text)
    if k:
        den, num = _KO_NUM[k.group(1)], _KO_NUM[k.group(2)]  # "N분의 M박자": M beats of 1/N notes
        cands.append((k.start(), (num, den)))
    b = _KO_BEATS_RE.search(text)
    if b and not k:
        cands.append((b.start(), _BEATS_TO_SIG[_KO_NUM[b.group(1)]]))
    for _pos, sig in sorted(cands):
        if sig in TIME_SIGNATURES:
            return sig
    return None


