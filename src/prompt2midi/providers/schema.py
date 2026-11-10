"""Provider-specific JSON Schemas derived from the MusicSpec schema.

Both providers only accept a subset of JSON Schema for structured outputs (docs/providers.md):
- Anthropic: no numeric/length constraints, minItems only 0/1, additionalProperties must be false.
- OpenAI strict mode: every property must be required (optional = union with null), additionalProperties false,
  at most 5000 properties and 10 nesting levels.
Constraints we drop here are enforced afterwards by our own validators (the pipeline validates every answer).
"""
from __future__ import annotations

import copy
from typing import Any

OPENAI_MAX_PROPERTIES = 5000
OPENAI_MAX_DEPTH = 10


def _walk(node: Any, fn) -> Any:
    if isinstance(node, dict):
        node = fn(node)
        return {k: _walk(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(v, fn) for v in node]
    return node


def _tuples_to_arrays(node: dict) -> dict:
    """prefixItems (tuple) is not in either provider's documented subset: use a fixed-length array."""
    if "prefixItems" not in node:
        return node
    node = dict(node)
    items = node.pop("prefixItems")
    node["items"] = items[0] if items else {}
    node["minItems"] = node["maxItems"] = len(items)
    return node


def _drop_noise(node: dict) -> dict:
    return {k: v for k, v in node.items() if k not in ("default", "examples")}


def base_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _walk(copy.deepcopy(schema), lambda n: _drop_noise(_tuples_to_arrays(n)))


def schema_for_anthropic(schema: dict[str, Any]) -> dict[str, Any]:
    """Schema for output_config.format. Uses the SDK's own transform_schema (moves unsupported
    constraints into descriptions and sets additionalProperties=false)."""
    from anthropic import transform_schema

    return transform_schema(base_schema(schema))


def _is_nullable(prop: dict) -> bool:
    if prop.get("type") == "null":
        return True
    return any(isinstance(a, dict) and a.get("type") == "null" for a in prop.get("anyOf", []))


def _strictify(node: dict) -> dict:
    if node.get("type") == "object" and "properties" in node:
        node = dict(node)
        required = set(node.get("required", []))
        props = {}
        for name, prop in node["properties"].items():
            if name not in required and not _is_nullable(prop):
                prop = {"anyOf": [prop, {"type": "null"}]}
            props[name] = prop
        node["properties"] = props
        node["required"] = list(props)
        node["additionalProperties"] = False
    return node


def schema_for_openai(schema: dict[str, Any]) -> dict[str, Any]:
    """Schema for strict structured outputs: all properties required, optional ones nullable."""
    return _walk(base_schema(schema), _strictify)


def strip_nulls(value: Any) -> Any:
    """Undo the 'optional = nullable' trick: drop keys whose value is null (recursively)."""
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    return value


# ---- structural checks used by tests and by a startup self-check ----

def count_properties(schema: Any) -> int:
    if isinstance(schema, dict):
        n = len(schema.get("properties", {})) if isinstance(schema.get("properties"), dict) else 0
        return n + sum(count_properties(v) for v in schema.values())
    if isinstance(schema, list):
        return sum(count_properties(v) for v in schema)
    return 0


def object_depth(schema: Any, defs: dict | None = None, seen: tuple = ()) -> int:
    """Deepest chain of nested objects, following $ref."""
    defs = defs if defs is not None else (schema.get("$defs", {}) if isinstance(schema, dict) else {})
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name in seen:
                return 0
            return object_depth(defs.get(name, {}), defs, seen + (name,))
        own = 1 if schema.get("type") == "object" else 0
        inner = [object_depth(v, defs, seen) for k, v in schema.items() if k != "$defs"]
        return own + max(inner, default=0)
    if isinstance(schema, list):
        return max((object_depth(v, defs, seen) for v in schema), default=0)
    return 0
