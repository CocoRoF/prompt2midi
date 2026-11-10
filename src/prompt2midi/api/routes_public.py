"""Public API: /healthz, /api/providers, /api/generate, /api/midi/{id}.mid (no authentication)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from ..pipeline import GenerationError, generate
from ..providers import ALL_IDS, LABELS, LLM_IDS, build_provider
from .deps import client_ip, state
from .errors import ApiError
from .store import PROVIDER_IDS

router = APIRouter()
HARD_MAX_PROMPT = 2000  # the pipeline's own cap


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: Annotated[str, Field(min_length=1, max_length=HARD_MAX_PROMPT)]
    provider: Literal["rule", "anthropic", "openai"] | None = None
    seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)] | None = None
    fallback: StrictBool = True


def provider_available(st, pid: str, *, public: bool = True) -> bool:
    """rule: always. LLM: key stored + model set + enabled by the admin (+ public_llm when `public`)."""
    if pid == "rule":
        return True
    if pid not in PROVIDER_IDS:
        return False
    cfg = st.store.settings()
    p = cfg["providers"][pid]
    ok = bool(st.store.get_key(pid)) and bool((p.get("model") or "").strip()) and bool(p.get("enabled"))
    return ok and (cfg["public_llm"] if public else True)


def make_provider(st, pid: str):
    if pid == "rule":
        return build_provider("rule")
    return build_provider(
        pid,
        key_source=lambda: st.store.get_key(pid),
        model_source=lambda: st.store.settings()["providers"][pid].get("model", ""),
        adapter=(st.adapters or {}).get(pid),
    )


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@router.get("/api/providers")
def providers(request: Request) -> list[dict]:
    st = state(request)
    return [{"id": pid, "label": LABELS[pid], "available": provider_available(st, pid)} for pid in ALL_IDS]


def _problems(st, items) -> list[dict]:
    return [{"path": getattr(i, "path", ""), "message": st.scrub(getattr(i, "message", str(i)))} for i in items]


def _attempts(st, attempts) -> list[dict]:
    return [{"provider": a.provider, "ok": a.ok, "errors": [{"path": e.get("path", ""), "message": st.scrub(e.get("message", ""))} for e in a.issues]} for a in attempts]


@router.post("/api/generate")
def generate_endpoint(body: GenerateIn, request: Request) -> dict:
    st = state(request)
    cfg = st.store.settings()
    limits = cfg["limits"]
    ip = client_ip(request)

    if not st.rate.allow_and_record(ip, limits["requests_per_minute"]):
        raise ApiError(429, "rate_limited", "too many requests; wait a minute and try again", headers={"Retry-After": "60"})
    if len(body.prompt) > limits["max_prompt_chars"]:
        raise ApiError(400, "invalid_input", f"the prompt is longer than {limits['max_prompt_chars']} characters",
                       [{"path": "prompt", "message": f"at most {limits['max_prompt_chars']} characters"}])
    if not body.prompt.strip():
        raise ApiError(400, "invalid_input", "the prompt is empty", [{"path": "prompt", "message": "must not be blank"}])

    explicit = body.provider is not None
    pid = body.provider or cfg["default_provider"]
    if pid in LLM_IDS:
        if not cfg["public_llm"]:
            if explicit:
                raise ApiError(403, "llm_disabled", "LLM providers are not enabled for public use")
            pid = "rule"  # the admin's default is an LLM but it is switched off: serve the rule provider
        elif not provider_available(st, pid):
            raise ApiError(503, "provider_unavailable", f"{LABELS[pid]} is not available right now")
        elif st.store.usage_today(st.today()) >= limits["daily_llm_cap"]:
            raise ApiError(429, "daily_cap_reached", "the daily limit for LLM requests has been reached", headers={"Retry-After": "3600"})

    try:
        result = generate(body.prompt, provider=make_provider(st, pid), max_retries=limits["max_retries"], fallback=body.fallback, seed=body.seed)
    except GenerationError as exc:
        _count_llm(st, exc.attempts)
        raise ApiError(422, "generation_failed", "no valid MIDI could be produced", {"attempts": _attempts(st, exc.attempts)}) from None
    except ValueError as exc:
        raise ApiError(400, "invalid_input", str(exc)) from None
    _count_llm(st, result.attempts)
    mid = st.midis.put(result.midi)
    return {
        "id": mid,
        "spec": result.spec.model_dump(mode="json", by_alias=True),
        "midi_url": f"/api/midi/{mid}.mid",
        "provider_used": result.provider_used,
        "seed": result.seed,
        "warnings": _problems(st, result.warnings),
        "attempts": _attempts(st, result.attempts),
    }


def _count_llm(st, attempts) -> None:
    n = sum(1 for a in attempts if a.provider in LLM_IDS)
    if n:
        st.store.add_usage(st.today(), n)


@router.get("/api/midi/{mid}.mid")
def midi(mid: str, request: Request) -> Response:
    data = state(request).midis.get(mid)
    if data is None:
        raise ApiError(404, "not_found", "no such MIDI file (it may have expired)")
    return Response(data, media_type="audio/midi", headers={"Content-Disposition": f'attachment; filename="prompt2midi-{mid[:8]}.mid"', "Cache-Control": "private, max-age=0"})
