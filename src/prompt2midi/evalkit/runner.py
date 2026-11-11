"""Generate for every prompt, then score. Offline: rule / fake providers only."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from ..pipeline import GenerationError, generate
from ..providers import FakeProvider, RuleProvider
from ..spec import total_bars
from .metrics import METRICS, Context, Result
from .model import EvalSet

PROVIDERS = ("rule", "fake")  # "llm" is accepted by the CLI but never run here (no keys, no calls)

# What the `fake` provider answers to every prompt: a fixed, valid spec (so the harness itself can be
# tested and so a run has a known, prompt-independent baseline).
FAKE_SPEC = {
    "version": "1", "title": "evalkit fake", "seed": 1, "tempo_bpm": 100, "time_signature": [4, 4],
    "key": {"tonic": "C", "mode": "major"},
    "sections": [{"name": "A", "bars": 4, "chords": ["C", "F", "G", "C"], "energy": 0.5},
                 {"name": "B", "bars": 4, "chords": ["Am", "F", "G", "C"], "energy": 0.6},
                 {"name": "C", "bars": 4, "chords": ["C", "G", "F", "C"], "energy": 0.4}],
    "tracks": [{"role": "melody", "instrument": "piano", "motif": {"degrees": [1, 3, 5, 3], "rhythm": ["q", "e", "e", "q"]}, "register": [60, 84]},
               {"role": "bass", "instrument": "electric_bass"}, {"role": "drums", "style": "rock_basic"}],
}


def make_provider(name: str):
    if name == "rule":
        return RuleProvider()
    if name == "fake":
        return FakeProvider.fixed(copy.deepcopy(FAKE_SPEC))
    raise ValueError(f"unsupported provider {name!r}; evalkit runs {PROVIDERS} only")


@dataclass
class ItemResult:
    id: str
    lang: str
    tier: str
    generated: bool
    error: str
    provider_used: str
    attempts: int
    fallback_used: bool
    bars: int | None
    midi_sha8: str
    results: dict[str, Result] = field(default_factory=dict)


def run_set(eval_set: EvalSet, provider: str, *, fallback: bool = False, max_retries: int = 2, check_determinism: bool = True) -> list[ItemResult]:
    """fallback=False by default so the numbers describe the chosen provider alone."""
    out = []
    for item in eval_set.prompts:
        prov = make_provider(provider)  # fresh per prompt: a FakeProvider keeps call history
        spec = midi = None
        err, used, attempts, fb = "", provider, 0, False
        try:
            res = generate(item.text, provider=prov, fallback=fallback, max_retries=max_retries)
            spec, midi, used, attempts, fb = res.spec, res.midi, res.provider_used, len(res.attempts), res.fallback_used
        except GenerationError as exc:
            attempts, err = len(exc.attempts), str(exc)[:300]
        except ValueError as exc:  # prompt rejected by generate() (e.g. too long)
            err = str(exc)[:300]
        det = None
        if check_determinism and midi is not None:
            try:
                again = generate(item.text, provider=make_provider(provider), fallback=fallback, max_retries=max_retries)
                det = again.midi == midi
            except (GenerationError, ValueError):
                det = False
        ctx = Context(item.expect, spec, midi, err, det)
        out.append(ItemResult(
            item.id, item.lang, item.tier, midi is not None, err, used if midi is not None else "", attempts, fb,
            total_bars(spec) if spec is not None else None, hashlib.sha256(midi).hexdigest()[:8] if midi else "",
            {m.name: m.fn(ctx) for m in METRICS},
        ))
    return out
