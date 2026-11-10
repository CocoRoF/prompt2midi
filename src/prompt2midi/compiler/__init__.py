"""Compiler: MusicSpec + seed -> MIDI bytes (deterministic)."""
from .render import PPQ, CompileError, compile_spec

__all__ = ["PPQ", "CompileError", "compile_spec"]
