"""The frozen baseline-v0 samples: files exist, are valid MIDI, match the listing and the set; the proxy indicators run over them."""
import csv
import json
from pathlib import Path

from prompt2midi import evalkit
from prompt2midi.evalkit import __main__ as cli
from prompt2midi.evalkit import proxy as P
from prompt2midi.validate import check_midi

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "samples" / "baseline-v0"


def test_samples_match_the_set_and_the_listing():
    eval_set, _ = evalkit.load_set(DIR / "set.json")
    ids = [p.id for p in eval_set.prompts]
    assert len(ids) == 16 and ids[:10] == [f"r{i:02d}" for i in range(1, 11)]
    assert sorted(f.stem for f in DIR.glob("*.mid")) == sorted(ids)            # file name = prompt id
    readme = (DIR / "README.md").read_text(encoding="utf-8")
    assert all(f"| `{i}` |" in readme for i in ids) and "생성 시점 코드 커밋" in readme
    assert {p.lang for p in eval_set.prompts} == {"en", "ko"}


def test_samples_are_valid_midi_and_the_proxies_run_without_a_spec():
    for f in sorted(DIR.glob("*.mid")):
        data = f.read_bytes()
        assert not [i for i in check_midi(data) if i.severity == "error"], f.name
        res = P.evaluate(data)                                                    # external-MIDI mode: no spec, no key
        assert res["m2"].status != P.NA, f.name
        assert [res[k].status for k in ("m4", "m5", "m6", "m7")] == [P.NA] * 4, f.name   # these need a key / sections / chord names


def test_run_over_the_samples_is_deterministic(tmp_path):
    for d in ("a", "b"):
        assert cli.main([str(DIR / "set.json"), "--provider", "rule", "--out", str(tmp_path / d)]) == 0
    for f in ("results.csv", "summary.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()
    md = (tmp_path / "a" / "summary.md").read_text(encoding="utf-8")
    assert "## 대리 지표 M1~M7 — 합격 판정이 아님" in md and "임시값" in md and "품질의 근거나 서명 지표로 쓰지 않는다" in md
    rows = list(csv.DictReader(open(tmp_path / "a" / "results.csv", encoding="utf-8")))
    assert list(rows[0])[-len(P.column_names()):] == P.column_names() and len(rows) == 16
    assert all(r[f"proxy_{k}"] in ("in_range", "out_of_range", "na") for r in rows for k in ("m1", "m2", "m3", "m4", "m5", "m6", "m7"))
    # the proxy verdicts are not pass/fail and are not part of axis1_ok: pass/fail columns are unchanged by them
    assert all(r["axis1_ok"] in ("True", "False") for r in rows)


def test_the_document_quotes_this_runs_statuses_and_values(tmp_path):
    cli.main([str(DIR / "set.json"), "--provider", "rule", "--out", str(tmp_path)])
    rows = list(csv.DictReader(open(tmp_path / "results.csv", encoding="utf-8")))
    doc = (ROOT / "docs" / "baseline-expressiveness-v0.md").read_text(encoding="utf-8")
    for r in rows:
        statuses = {"in_range", "out_of_range", "na"}
        for line in doc.splitlines():                                                          # the status table: `id` | seven verdicts
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] == f"`{r['id']}`" and len(cells) == 8 and set(cells[1:]) <= statuses:
                assert cells[1:] == [r[f"proxy_m{i}"] for i in range(1, 8)], r["id"]
                break
        else:
            raise AssertionError(f"{r['id']} is missing from the status table")
