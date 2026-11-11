"""The frozen baseline-v0 samples: files exist, are valid MIDI, match the listing and the set; indicators run over them."""
import csv
import json
from pathlib import Path

from prompt2midi import evalkit
from prompt2midi.evalkit import __main__ as cli
from prompt2midi.evalkit import expressiveness as E
from prompt2midi.validate import check_midi

DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "baseline-v0"


def test_samples_match_the_set_and_the_listing():
    eval_set, _ = evalkit.load_set(DIR / "set.json")
    ids = [p.id for p in eval_set.prompts]
    assert len(ids) == 16 and ids[:10] == [f"r{i:02d}" for i in range(1, 11)]
    assert sorted(f.stem for f in DIR.glob("*.mid")) == sorted(ids)            # file name = prompt id
    readme = (DIR / "README.md").read_text(encoding="utf-8")
    assert all(f"| `{i}` |" in readme for i in ids) and "생성 시점 코드 커밋" in readme
    langs = {p.lang for p in eval_set.prompts}
    assert langs == {"en", "ko"}


def test_samples_are_valid_midi_and_the_indicators_measure_them():
    for f in sorted(DIR.glob("*.mid")):
        data = f.read_bytes()
        assert not [i for i in check_midi(data) if i.severity == "error"], f.name
        values = E.measure(data)                                                 # without a spec: only the spec-free indicators
        assert values["melody_repetition"] is not None and values["step_ratio"] is not None, f.name
        assert values["section_density_gap"] is None and values["final_is_tonic"] is None


def test_run_over_the_samples_is_deterministic_and_reports_the_expressiveness_section(tmp_path):
    for d in ("a", "b"):
        assert cli.main([str(DIR / "set.json"), "--provider", "rule", "--out", str(tmp_path / d)]) == 0
    for f in ("results.csv", "summary.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()
    md = (tmp_path / "a" / "summary.md").read_text(encoding="utf-8")
    assert "## 표현력 대리 지표 (값 — 합격 판정도 음악 품질도 아님)" in md and "값이 높거나 낮은 것이 좋은 음악·나쁜 음악을 뜻하지 않는다" in md
    rows = list(csv.DictReader(open(tmp_path / "a" / "results.csv", encoding="utf-8")))
    cols = list(rows[0])
    assert cols[-len(E.INDICATORS):] == [E.COLUMN_PREFIX + i.name for i in E.INDICATORS]    # appended at the very end
    assert all(r["expr_melody_repetition"] != "" for r in rows) and len(rows) == 16
    # the committed document quotes the numbers of this very run
    doc = (Path(__file__).resolve().parent.parent / "docs" / "baseline-expressiveness-v0.md").read_text(encoding="utf-8")
    for r in rows:
        line = next(l for l in doc.splitlines() if l.startswith(f"| `{r['id']}` |"))
        cells = [c.strip() for c in line.strip("|").split("|")[1:]]
        got = [r["expr_" + n] for n in ("melody_repetition", "melody_variation", "step_ratio", "leap_ratio", "contour_diversity", "final_is_tonic",
                                         "section_density_gap", "section_register_gap", "chord_voice_distance", "off_scale_rate")]
        assert [None if c == "—" else float(c) for c in cells] == [None if g == "" else float(g) for g in got], r["id"]


def test_final_tonic_is_zero_for_every_baseline_sample(tmp_path):
    """The measured baseline fact quoted in the document (16 of 16 melodies do not end on the tonic)."""
    cli.main([str(DIR / "set.json"), "--provider", "rule", "--out", str(tmp_path)])
    rows = list(csv.DictReader(open(tmp_path / "results.csv", encoding="utf-8")))
    assert [r["expr_final_is_tonic"] for r in rows] == ["0.0"] * 16
