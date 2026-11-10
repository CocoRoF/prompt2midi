"""FakeProvider: fixed or recorded responses for tests. Never touches the network."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import ProviderError


@dataclass
class Call:
    prompt: str
    feedback: list[dict[str, str]] | None
    seed: int | None


@dataclass
class FakeProvider:
    """Returns `responses` in order. An item may be a dict, a JSON string (even broken JSON), or an
    Exception instance (raised, to simulate a provider failure). When the list runs out the last item
    repeats if `repeat_last`, otherwise ProviderError is raised.
    """

    responses: list[Any]
    name: str = "fake"
    repeat_last: bool = False
    calls: list[Call] = field(default_factory=list)

    @classmethod
    def fixed(cls, spec: dict[str, Any], name: str = "fake") -> "FakeProvider":
        return cls([spec], name=name, repeat_last=True)

    @classmethod
    def from_fixture(cls, path: str | Path, name: str = "fake") -> "FakeProvider":
        """Load recorded responses: a JSON list whose items are objects or strings."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("fixture must be a JSON list of responses")
        return cls(data, name=name)

    def generate_spec(self, prompt, schema, *, feedback=None, seed=None):
        self.calls.append(Call(prompt, None if feedback is None else [dict(f) for f in feedback], seed))
        i = len(self.calls) - 1
        if i >= len(self.responses):
            if self.repeat_last and self.responses:
                i = len(self.responses) - 1
            else:
                raise ProviderError("FakeProvider has no more recorded responses")
        item = self.responses[i]
        if isinstance(item, BaseException):
            raise item
        return item
