"""Shared result types for the validators."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    """One problem found by a validator. `path` is a JSON-pointer-like location ('sections[0].chords[2]')."""

    path: str
    message: str
    code: str = "invalid"
    severity: str = "error"  # "error" | "warning"

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code, "severity": self.severity}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def feedback(self) -> list[dict[str, str]]:
        """Error list in the shape sent back to an LLM for a retry: [{path, message}]."""
        return [{"path": i.path, "message": i.message} for i in self.errors]
