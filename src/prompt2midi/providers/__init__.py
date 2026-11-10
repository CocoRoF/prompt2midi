"""Providers: anything that turns a prompt into a MusicSpec (dict or JSON text).

Only offline providers exist so far (FakeProvider for tests, RuleProvider as the key-less fallback).
"""
from .base import Feedback, LLMProvider, ProviderError
from .fake import FakeProvider
from .rule import RuleProvider

__all__ = ["FakeProvider", "Feedback", "LLMProvider", "ProviderError", "RuleProvider"]
