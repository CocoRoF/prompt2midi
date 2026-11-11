"""Expander: Intent v1 -> MusicSpec (deterministic) with an explain log. See docs/expander.md."""
from .core import Decision, ExpandError, Expansion, derive_seed, expand
from .presets import GENRES, GenrePreset, assign_instruments

__all__ = ["Decision", "ExpandError", "Expansion", "GENRES", "GenrePreset", "assign_instruments", "derive_seed", "expand"]
