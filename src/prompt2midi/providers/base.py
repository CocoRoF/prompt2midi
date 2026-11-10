"""LLM provider protocol. Providers only produce a spec (dict or raw JSON text); they never write MIDI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider could not produce a response (transport, quota, no recorded answer left...).

    Messages must never contain credentials.
    """


@dataclass(frozen=True)
class Feedback:
    """One validation problem sent back to the provider on a retry."""

    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def generate_spec(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        feedback: list[dict[str, str]] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any] | str:
        """Return a MusicSpec as a dict or as JSON text. `feedback` lists the errors of the previous try."""
        ...
