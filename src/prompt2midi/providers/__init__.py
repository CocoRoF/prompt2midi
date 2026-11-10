"""Providers: anything that turns a prompt into a MusicSpec (dict or JSON text).

FakeProvider (tests), RuleProvider (offline fallback), AnthropicProvider / OpenAIProvider (keys come from
the application at call time, never from this package).
"""
from .base import Feedback, LLMProvider, ProviderError
from .fake import FakeProvider
from .llm import AnthropicProvider, OpenAIProvider
from .registry import ALL_IDS, LABELS, LLM_IDS, build_provider
from .rule import RuleProvider
from .secrets import mask_secrets

__all__ = ["ALL_IDS", "AnthropicProvider", "FakeProvider", "Feedback", "LABELS", "LLMProvider", "LLM_IDS", "OpenAIProvider",
           "ProviderError", "RuleProvider", "build_provider", "mask_secrets"]
