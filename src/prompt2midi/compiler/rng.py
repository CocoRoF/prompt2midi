"""Small, version-independent PRNG (mulberry32) so output bytes never depend on the Python version."""
from __future__ import annotations


def _fnv1a(text: str) -> int:
    h = 2166136261
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


class Rng:
    def __init__(self, seed: int, label: str = ""):
        self._a = (seed ^ _fnv1a(label)) & 0xFFFFFFFF

    def random(self) -> float:
        self._a = (self._a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self._a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    def int(self, lo: int, hi: int) -> int:
        """Integer in [lo, hi] inclusive."""
        return lo + int(self.random() * (hi - lo + 1))

    def choice(self, items):
        return items[int(self.random() * len(items))]

    def jitter(self, amount: int) -> int:
        return self.int(-amount, amount)
