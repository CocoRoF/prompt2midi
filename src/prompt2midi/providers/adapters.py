"""Thin adapters around the vendor SDKs. Everything vendor-specific lives here so tests can swap it out."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AdapterResult:
    text: str  # the JSON text the model produced ('' if none)
    stop_reason: str | None = None  # "end", "refusal", "truncated" or the vendor's own word
    refusal: str | None = None


class LLMAdapter(Protocol):
    def complete_json(self, *, model: str, api_key: str, system: str, user: str, schema: dict[str, Any], max_tokens: int, timeout: float) -> AdapterResult:
        """One request. May raise any SDK exception; the caller masks the key out of it."""
        ...


class AnthropicSDKAdapter:
    """Messages API with structured output (output_config.format = json_schema).

    Docs (read 2026-11-10): https://platform.claude.com/docs/en/build-with-claude/structured-outputs
    """

    def __init__(self, http_client: Any | None = None, base_url: str | None = None):
        self._http_client = http_client
        self._base_url = base_url

    def complete_json(self, *, model, api_key, system, user, schema, max_tokens, timeout):
        import anthropic

        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}  # retries are the pipeline's job
        if self._http_client is not None:
            kwargs["http_client"] = self._http_client
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = anthropic.Anthropic(**kwargs)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
            timeout=timeout,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            return AdapterResult(text, "refusal", text or "refused")
        if stop == "max_tokens":
            return AdapterResult(text, "truncated")
        return AdapterResult(text, "end")


class OpenAISDKAdapter:
    """Responses API with strict structured output (text.format = json_schema, strict true).

    Docs (read 2026-11-10): https://developers.openai.com/api/docs/guides/structured-outputs
    """

    def __init__(self, http_client: Any | None = None, base_url: str | None = None):
        self._http_client = http_client
        self._base_url = base_url

    def complete_json(self, *, model, api_key, system, user, schema, max_tokens, timeout):
        import openai

        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if self._http_client is not None:
            kwargs["http_client"] = self._http_client
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = openai.OpenAI(**kwargs)
        resp = client.responses.create(
            model=model,
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": "music_spec", "strict": True, "schema": schema}},
            max_output_tokens=max_tokens,
            store=False,
            timeout=timeout,
        )
        refusal = None
        for item in getattr(resp, "output", None) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    refusal = getattr(part, "refusal", None) or "refused"
        text = resp.output_text or ""
        if refusal is not None:
            return AdapterResult(text, "refusal", refusal)
        if getattr(resp, "status", None) == "incomplete":
            return AdapterResult(text, "truncated")
        return AdapterResult(text, "end")
