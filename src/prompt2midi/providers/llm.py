"""LLM providers (Anthropic, OpenAI): a prompt becomes a MusicSpec through a vendor SDK adapter.

Keys and model names are read at call time from the callables given at construction, so a key changed
in the admin panel applies to the next request. No key or model, no call. Nothing here reads
environment variables, files, or defaults for keys, and no exception leaves this module with a key in it.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .adapters import AdapterResult, AnthropicSDKAdapter, LLMAdapter, OpenAISDKAdapter
from .base import ProviderError
from .prompts import SYSTEM_PROMPT, build_user_message
from .schema import schema_for_anthropic, schema_for_openai, strip_nulls
from .secrets import mask_secrets

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 60.0
PING_MAX_TOKENS = 64


class _LLMProvider:
    name = ""
    _make_adapter: Callable[[], LLMAdapter]

    def __init__(self, key_source: Callable[[], str | None], model_source: Callable[[], str | None],
                 adapter: LLMAdapter | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = DEFAULT_TIMEOUT):
        self._key_source = key_source
        self._model_source = model_source
        self._adapter = adapter
        self.max_tokens = max_tokens
        self.timeout = timeout

    # -- subclass hooks --
    def _schema(self, schema: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _post(self, data: Any) -> Any:
        return data

    def available(self) -> bool:
        """True when a key and a model name are configured (says nothing about the key being valid)."""
        return bool(self._key_source()) and bool((self._model_source() or "").strip())

    def _request(self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> AdapterResult:
        """One adapter call with the current key/model. Every failure becomes a ProviderError without the key."""
        key = self._key_source()
        model = (self._model_source() or "").strip()
        if not key:
            raise ProviderError(f"{self.name} has no API key configured")
        if not model:
            raise ProviderError(f"{self.name} has no model configured")
        adapter = self._adapter or self._make_adapter()
        failure: str | None = None
        result: AdapterResult | None = None
        try:
            result = adapter.complete_json(model=model, api_key=key, system=system, user=user, schema=schema,
                                           max_tokens=max_tokens, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - SDK errors are many; all are masked and re-raised as ProviderError
            status = getattr(exc, "status_code", None)
            detail = mask_secrets(str(exc), [key])[:300]
            failure = f"{self.name} request failed: {type(exc).__name__}" + (f" (HTTP {status})" if status else "") + (f": {detail}" if detail else "")
        if failure is not None:
            raise ProviderError(failure) from None  # raised outside the except block: no key-bearing __context__
        assert result is not None
        if result.stop_reason == "refusal":
            raise ProviderError(f"{self.name} refused the request: {mask_secrets(result.refusal or '', [key])[:200]}")
        if result.stop_reason == "truncated":
            raise ProviderError(f"{self.name} output was cut off (token limit); shorten the piece (fewer sections, shorter motif)")
        return result

    def generate_spec(self, prompt: str, schema: dict[str, Any], *, feedback: list[dict[str, str]] | None = None, seed: int | None = None):
        result = self._request(system=SYSTEM_PROMPT, user=build_user_message(prompt, feedback), schema=self._schema(schema), max_tokens=self.max_tokens)
        text = (result.text or "").strip()
        if not text:
            raise ProviderError(f"{self.name} returned an empty answer")
        try:
            data = json.loads(text)
        except ValueError:
            return text  # let the pipeline report the JSON error back to the model
        return self._post(data)

    def ping(self) -> None:
        """Smallest possible real request (admin 'connection test'). Raises ProviderError on any failure."""
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        result = self._request(system="Answer with the JSON object {\"ok\": true}.", user="ping", schema=schema, max_tokens=PING_MAX_TOKENS)
        if not (result.text or "").strip():
            raise ProviderError(f"{self.name} returned an empty answer")


class AnthropicProvider(_LLMProvider):
    name = "anthropic"

    def _make_adapter(self) -> LLMAdapter:
        return AnthropicSDKAdapter()

    def _schema(self, schema):
        return schema_for_anthropic(schema)


class OpenAIProvider(_LLMProvider):
    name = "openai"

    def _make_adapter(self) -> LLMAdapter:
        return OpenAISDKAdapter()

    def _schema(self, schema):
        return schema_for_openai(schema)

    def _post(self, data):
        return strip_nulls(data)
