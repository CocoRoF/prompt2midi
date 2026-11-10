"""Provider ids, labels and construction. Keys/models come from callables supplied by the application."""
from __future__ import annotations

from typing import Callable

from .base import LLMProvider
from .llm import AnthropicProvider, OpenAIProvider
from .rule import RuleProvider

LABELS = {"rule": "Rule (offline)", "anthropic": "Anthropic", "openai": "OpenAI"}
LLM_IDS = ("anthropic", "openai")
ALL_IDS = ("rule",) + LLM_IDS


def build_provider(provider_id: str, *, key_source: Callable[[], str | None] | None = None,
                   model_source: Callable[[], str | None] | None = None, adapter=None, **options) -> LLMProvider:
    if provider_id == "rule":
        return RuleProvider()
    if provider_id not in LLM_IDS:
        raise ValueError(f"unknown provider: {provider_id!r}")
    if key_source is None or model_source is None:
        raise ValueError("LLM providers need key_source and model_source")
    cls = AnthropicProvider if provider_id == "anthropic" else OpenAIProvider
    return cls(key_source, model_source, adapter=adapter, **options)
