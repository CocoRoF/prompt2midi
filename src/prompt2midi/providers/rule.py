"""RuleProvider: a sentence becomes a MusicSpec through three deterministic steps. No network, no keys.

    sentence --translate (lexicon words, key/meter/BPM patterns)--> Intent v1 --expander (presets)--> MusicSpec

`translate` only extracts what the sentence states; every musical decision (defaults, genre presets, instrument roles, chords)
is the expander's. Words live in prompt2midi/translate/lexicon/*.json, numbers and tables in prompt2midi/presets.py and
prompt2midi/expander/. The same prompt always gives the same dict. `RuleProvider.explain(prompt)` shows both steps.
Rules, priorities and the old-code correspondence: docs/rule-provider.md.
"""
from __future__ import annotations

from typing import Any

from ..expander import expand
from ..presets import DEFAULT_TONIC, MOOD_ORDER, MOODS, MOTIFS, NAMES, PROGRESSIONS, triad as _triad  # noqa: F401  (re-exported for compatibility)
from ..translate import translate
from ..translate.keys import find_key, find_mode_word, find_time_signature  # noqa: F401  (re-exported for compatibility)

MAX_PROMPT_CHARS = 2000
DEFAULT_MOOD = "calm"


class RuleProvider:
    """Prompt text -> spec dict by dictionary rules. Name: 'rule'."""

    name = "rule"

    def generate_spec(self, prompt: str, schema: dict[str, Any] | None = None, *, feedback=None, seed: int | None = None) -> dict[str, Any]:
        spec, _ = self._build(prompt)
        if seed is not None:
            spec["seed"] = seed
        return spec

    def explain(self, prompt: str) -> dict[str, Any]:
        """What the rules decided and why: legacy summary keys plus the Intent that was extracted and the expander's decisions."""
        return self._build(prompt)[1]

    # ------------------------------------------------------------------
    def _build(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        ex = translate(prompt)
        intent = ex.intent
        exp = expand(intent, instruments_mode="fill")
        s = exp.spec
        spec = s.model_dump(mode="json", by_alias=True, exclude_none=True)
        spec.pop("seed", None)  # the RuleProvider has never fixed a seed: the compiler derives it from the spec content
        mood = intent.mood[0].value if intent.mood else DEFAULT_MOOD
        spec["title"] = f"{mood.capitalize()} {s.key.tonic.value} {s.key.mode.value.replace('_', ' ')}"[:60]  # historical title format

        by_field = {d.field: d for d in exp.explain}
        defaults: list[str] = []
        if not intent.mood:
            defaults.append("mood=calm (no mood word)")
        if intent.tonic is None:
            mw = intent.mode is not None
            defaults.append(f"key={s.key.tonic.value} {s.key.mode.value} ({'mode word' if mw else 'from mood'})")
        if intent.time_signature is None:
            defaults.append("time_signature=4/4")
        if intent.tempo_bpm is None:
            defaults.append(f"tempo={s.tempo_bpm} (from {by_field['tempo_bpm'].source.split(':')[0]})")
        roles = {t.role.value: (t.instrument.value if t.instrument else t.style.value) for t in s.tracks}
        why: dict[str, Any] = {
            "defaults": defaults, "mood": mood, "key": f"{s.key.tonic.value} {s.key.mode.value}", "time_signature": list(s.time_signature),
            "tempo_bpm": s.tempo_bpm, "lead": roles.get("melody"), "bass": roles.get("bass"), "drums": roles.get("drums"),
            "words_seen": len(str(prompt)[:MAX_PROMPT_CHARS].split()),
            "intent": intent.model_dump(mode="json", exclude_defaults=True), "extraction": ex.trace, "expander": exp.explain_dicts(),
        }
        return spec, why
