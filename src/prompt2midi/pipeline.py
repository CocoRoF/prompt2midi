"""prompt -> provider -> validate -> compile -> MidiCheck, with error feedback retries and a rule fallback.

    generate("a calm piano piece", provider=FakeProvider(...), max_retries=2, fallback=True)

Every failure of the primary provider (transport error, broken JSON, schema or theory violation,
compiler refusal, MidiCheck error) becomes a list of {path, message} sent back on the next try. When
the tries are used up, the RuleProvider is asked once (if `fallback`), otherwise GenerationError is raised.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .compiler import CompileError, compile_spec
from .providers import LLMProvider, RuleProvider
from .spec import MusicSpec, effective_seed, json_schema
from .validate import Issue, check_midi, validate_spec

MAX_PROMPT_CHARS = 2000
MAX_RESPONSE_CHARS = 200_000
DEFAULT_RETRIES = 2


@dataclass
class Attempt:
    provider: str
    index: int  # 0-based try number for this provider
    ok: bool
    issues: list[dict[str, str]] = field(default_factory=list)  # what went wrong (empty when ok)
    sent_feedback: list[dict[str, str]] | None = None  # what the provider was told before this try


@dataclass
class GenerateResult:
    spec: MusicSpec
    midi: bytes
    provider_used: str
    fallback_used: bool
    seed: int
    attempts: list[Attempt]
    warnings: list[Issue]


class GenerationError(Exception):
    """No valid MIDI could be produced. `.attempts` holds the full history."""

    def __init__(self, message: str, attempts: list[Attempt]):
        super().__init__(message)
        self.attempts = attempts


def _short(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _try_once(provider: LLMProvider, prompt: str, schema: dict, feedback, seed):
    """One provider call + full checking. Returns (spec, midi, warnings, issues[dict])."""
    try:
        raw = provider.generate_spec(prompt, schema, feedback=feedback, seed=seed)
    except Exception as exc:  # providers may raise anything; never let it escape the loop
        return None, None, [], [{"path": "$", "message": f"provider failed: {type(exc).__name__}: {_short(str(exc))}"}]
    if isinstance(raw, (str, bytes)) and len(raw) > MAX_RESPONSE_CHARS:
        return None, None, [], [{"path": "$", "message": f"response is longer than {MAX_RESPONSE_CHARS} characters"}]
    spec, report = validate_spec(raw)
    if spec is None or not report.ok:
        return None, None, [], report.feedback()
    try:
        midi = compile_spec(spec, seed=seed)
    except CompileError as exc:
        return None, None, [], [{"path": i.path, "message": i.message} for i in exc.issues] or [{"path": "$", "message": str(exc)}]
    problems = [i for i in check_midi(midi, spec) if i.severity == "error"]
    if problems:  # a compiler bug, but never hand out a broken file
        return None, None, [], [{"path": i.path, "message": f"generated MIDI is invalid: {i.message}"} for i in problems]
    return spec, midi, report.warnings, []


def generate(
    prompt: str,
    *,
    provider: LLMProvider | None = None,
    max_retries: int = DEFAULT_RETRIES,
    fallback: bool = True,
    fallback_provider: LLMProvider | None = None,
    seed: int | None = None,
) -> GenerateResult:
    """Produce a validated MusicSpec and MIDI bytes for `prompt`.

    provider=None means RuleProvider. `max_retries` is the number of extra tries after the first one
    (so max_retries=2 allows 3 calls). `seed` (0..2**32-1) overrides the spec's own seed when compiling.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt is longer than {MAX_PROMPT_CHARS} characters")
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if seed is not None and not 0 <= seed < 2**32:
        raise ValueError("seed must be in 0..2**32-1")

    primary = provider or RuleProvider()
    schema = json_schema()
    attempts: list[Attempt] = []

    def run(prov: LLMProvider, tries: int) -> GenerateResult | None:
        feedback: list[dict[str, str]] | None = None
        for i in range(tries):
            spec, midi, warnings, issues = _try_once(prov, prompt, schema, feedback, seed)
            attempts.append(Attempt(prov.name, i, spec is not None, issues, feedback))
            if spec is not None:
                return GenerateResult(spec, midi, prov.name, prov is not primary, seed if seed is not None else effective_seed(spec), list(attempts), warnings)
            feedback = issues
        return None

    result = run(primary, max_retries + 1)
    if result is not None:
        return result
    if fallback:
        fb = fallback_provider or RuleProvider()
        if fb is not primary and getattr(fb, "name", None) != getattr(primary, "name", None):
            result = run(fb, 1)
            if result is not None:
                return result
    last = attempts[-1].issues
    raise GenerationError(
        f"no valid MIDI after {len(attempts)} attempt(s); last problems: {json.dumps(last, ensure_ascii=False)[:500]}",
        attempts,
    )
