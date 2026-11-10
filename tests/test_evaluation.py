import csv
import json
from pathlib import Path

from prompt2midi import evaluation as E

DATA = Path(__file__).resolve().parent.parent / "data"
PROMPTS = json.loads((DATA / "eval_prompts_research1.json").read_text(encoding="utf-8"))


def test_ten_research_prompts_all_valid_and_exact_on_explicit_values():
    rows = E.evaluate(PROMPTS, runs=1)
    assert len(rows) == 10
    assert all(r["valid"] and r["provider"] == "rule" and r["attempts"] == 1 for r in rows)
    assert all(r["tempo_match"] is True and r["key_match"] is True for r in rows)
    assert all(r["melody_in_declared_scale"] == 1.0 for r in rows)
    assert all(r["duration_s"] > 0 and r["notes"] > 0 and r["midi_bytes"] > 0 for r in rows)


def test_evaluation_is_reproducible():
    a = [r["sha256_8"] for r in E.evaluate(PROMPTS, runs=1)]
    b = [r["sha256_8"] for r in E.evaluate(PROMPTS, runs=1)]
    assert a == b and len(set(a)) == 10


def test_committed_results_match_a_fresh_run():
    """data/eval_rule_results.csv must be regenerated whenever the rules/compiler change (hashes only; timings vary)."""
    with open(DATA / "eval_rule_results.csv", encoding="utf-8") as f:
        committed = {int(r["id"]): r["sha256_8"] for r in csv.DictReader(f)}
    fresh = {r["id"]: r["sha256_8"] for r in E.evaluate(PROMPTS, runs=1)}
    assert committed == fresh, "stale data/eval_rule_results.csv: run python -m prompt2midi.evaluation data/eval_prompts_research1.json --out data/eval_rule_results.csv --md docs/rule-provider-eval.md"


def test_csv_and_summary(tmp_path):
    rows = E.evaluate(PROMPTS[:2], runs=1)
    out = tmp_path / "r.csv"
    E.write_csv(rows, out)
    with open(out, encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert list(got[0]) == E.COLUMNS and len(got) == 2
    md = E.summary_md(E.evaluate(PROMPTS, runs=1), "x.json")
    assert "10/10" in md and "text2midi" in md and "측정 안 함" in md


def test_prompts_file_is_the_research_team_file():
    assert [p["id"] for p in PROMPTS] == list(range(1, 11))
    assert {p["mood"] for p in PROMPTS} == {"calm", "sad", "happy", "energetic", "dark", "relaxing", "epic", "romantic", "playful", "meditative"}
