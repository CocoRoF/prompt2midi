"""Step 1 of validation: structure, types, ranges, closed enums, unknown fields."""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..spec import MusicSpec
from .issues import Issue


def _path(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "$"


def validate_schema(data: Any) -> tuple[MusicSpec | None, list[Issue]]:
    """Validate a dict / JSON string / bytes against MusicSpec. Never raises.

    Returns (spec, []) on success, (None, issues) otherwise. Unknown fields and unknown enum values
    are errors (nothing is silently dropped).
    """
    if isinstance(data, (str, bytes, bytearray)):
        try:
            data = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            return None, [Issue("$", f"not valid JSON: {exc}", "json")]
    if not isinstance(data, dict):
        return None, [Issue("$", f"spec must be a JSON object, got {type(data).__name__}", "type")]
    try:
        return MusicSpec.model_validate(data), []
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            msg = err["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            issues.append(Issue(_path(err["loc"]), msg, err["type"]))
        issues.sort(key=lambda i: (i.path, i.message))
        return None, issues
