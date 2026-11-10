"""Validation: schema (structure), theory (musical rules), midicheck (the generated MIDI itself)."""
from __future__ import annotations

from ..spec import MusicSpec
from .issues import Issue, Report
from .midicheck import check_midi
from .schema import validate_schema
from .theory import check_theory

__all__ = ["Issue", "Report", "check_midi", "check_theory", "validate_schema", "validate_spec"]


def validate_spec(data, *, strict_diatonic: bool = False) -> tuple[MusicSpec | None, Report]:
    """Schema check, then (only if the schema passed) theory check. Never raises."""
    spec, issues = validate_schema(data)
    if spec is None:
        return None, Report(issues)
    return spec, Report(check_theory(spec, strict_diatonic=strict_diatonic))
