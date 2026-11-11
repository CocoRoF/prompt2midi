"""Musical presets shared by the RuleProvider (prompt -> spec) and the expander (Intent -> spec).

One definition of the mood table, default progressions, motifs and diatonic chord spelling, so the two paths cannot drift apart.
The sound differences between these presets were NOT verified by listening.
"""
from __future__ import annotations

from .spec import MODE_INTERVALS

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


def triad(mode: str, tonic_pc: int, degree: int) -> str:
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




# interval sets (3rd, 5th, 7th above the chord root) -> chord quality spelled with the 10 qualities MusicSpec allows
_SEVENTH_QUALITY = {(4, 7, 11): "maj7", (4, 7, 10): "7", (3, 7, 10): "m7", (3, 6, 10): "m7b5"}


def diatonic_chord(mode: str, tonic_pc: int, degree: int, seventh: bool = False) -> str:
    """Chord built on a scale degree of `mode`. With seventh=True the 7th is stacked from the same scale when the result is
    one of maj7/7/m7/m7b5; otherwise (e.g. the minor-major 7th of harmonic minor) the plain triad is returned."""
    iv = MODE_INTERVALS[mode]
    base = triad(mode, tonic_pc, degree)
    if not seventh:
        return base
    root = iv[degree - 1]
    stack = tuple((iv[(degree - 1 + k) % 7] - root) % 12 for k in (2, 4, 6))
    quality = _SEVENTH_QUALITY.get(stack)
    if quality is None:
        return base
    return NAMES[(tonic_pc + root) % 12] + quality
