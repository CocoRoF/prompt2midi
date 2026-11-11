"""evalkit: offline evaluation harness (set file -> generate -> automatic scoring -> CSV + summary).

    python -m prompt2midi.evalkit <set.json> --provider rule|fake [--out DIR]

What it scores automatically are *properties that the prompt spells out in words* (tempo, key, time
signature, bars, instruments) plus validity, determinism and retry/fallback counts. It does NOT score
mood fit or musicality: those columns say "listen" and belong to a human rubric (연구 1팀).

Extension point (not in this version): Intent v1 field-match metrics (docs/eval-metrics.md, "확장 지점")
would register a new `Metric` in `metrics.METRICS` that reads an extracted intent instead of the MIDI.
"""
from .model import EvalItem, EvalSet, Expect, SetError, load_set
from .runner import ItemResult, run_set

__all__ = ["EvalItem", "EvalSet", "Expect", "SetError", "load_set", "ItemResult", "run_set"]
