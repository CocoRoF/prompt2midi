"""tools/compare_samples.py: 쌍 파일, 비교표, 결정성, v1 = 기존 동작, 입력 거절. 네트워크·키 없음."""
import csv
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import compare_samples as cs  # noqa: E402

from prompt2midi.compiler import compile_spec  # noqa: E402
from prompt2midi.evalkit.version import EVALKIT_VERSION  # noqa: E402
from prompt2midi.pipeline import generate  # noqa: E402
from prompt2midi.providers import RuleProvider  # noqa: E402
from prompt2midi.spec import effective_seed  # noqa: E402

SET = ROOT / "data/samples/baseline-v0/set.json"
IDS = "r01,r03,r08,s07"  # 영어·한국어, 4/4·3/4 가 섞이게


def run(tmp_path, name="o", *extra, ids=IDS):
    buf = io.StringIO()
    args = ["--out", str(tmp_path / name), "--commit", "abc1234", *extra]
    if ids:
        args += ["--ids", ids]
    rc = cs.main(args, out=buf)
    return rc, buf.getvalue(), tmp_path / name


def test_writes_pairs_table_and_csv(tmp_path):
    rc, text, d = run(tmp_path)
    assert rc == 0 and "4/4 쌍" in text
    names = sorted(p.name for p in d.iterdir())
    assert names == ["compare.csv", "compare.md"] + sorted(f"{i}.{e}.mid" for i in IDS.split(",") for e in ("v1", "v2"))
    for i in IDS.split(","):
        for e in ("v1", "v2"):
            assert (d / f"{i}.{e}.mid").read_bytes()[:4] == b"MThd"
    rows = list(csv.DictReader(open(d / "compare.csv", encoding="utf-8")))
    assert [r["id"] for r in rows] == IDS.split(",")
    for r in rows:
        for k in ("m1", "m2", "m3", "m4", "m5", "m6", "m7"):
            assert r[f"proxy_{k}_v1"] in ("in_range", "out_of_range", "na") and r[f"proxy_{k}_v2"] in ("in_range", "out_of_range", "na")
        assert r["v1_sha8"] and r["v2_sha8"] and r["error"] == ""
        assert r["same_midi"] in ("True", "False")
    # 모든 값 열이 v1·v2 쌍으로 있다
    cols = rows[0].keys()
    assert "proxy_m1_rep_rate_v1" in cols and "proxy_m1_rep_rate_v2" in cols and "proxy_m6_voicing_track_v2" in cols


def test_markdown_has_fixed_caveat_and_records(tmp_path):
    _, _, d = run(tmp_path)
    md = (d / "compare.md").read_text(encoding="utf-8")
    head = md.split("## 기록")[0]
    assert "임시 기준" in head and "음악이 좋아졌다는 뜻이 아니다" in head and "청취 미확인" in head
    assert "생성 커밋: `abc1234`" in md and f"`evalkit_version` {EVALKIT_VERSION}" in md
    assert "`--ids` r01,r03,r08,s07" in md and "baseline-v0 samples" in md
    for section in ("## 1. 지표별 판정 개수", "## 2. 곡별 판정", "## 3. 대표 값", "## 4. 이 비교가 보여 주지 못하는 것"):
        assert section in md
    # 시각·경로 없음
    assert not re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", md)
    assert str(tmp_path) not in md and "/workspace" not in md and "/tmp" not in md


def test_same_input_gives_byte_identical_output(tmp_path):
    _, _, a = run(tmp_path, "a")
    _, _, b = run(tmp_path, "b")
    assert sorted(p.name for p in a.iterdir()) == sorted(p.name for p in b.iterdir())
    for p in a.iterdir():
        assert p.read_bytes() == (b / p.name).read_bytes(), p.name


def test_v1_is_the_unchanged_engine_and_v2_differs_somewhere(tmp_path):
    _, _, d = run(tmp_path)
    prompts = {p["id"]: p["text"] for p in json.loads(SET.read_text(encoding="utf-8"))["prompts"]}
    differs = 0
    for i in IDS.split(","):
        res = generate(prompts[i], provider=RuleProvider(), fallback=False)
        spec = res.spec.model_copy(update={"engine": None})
        seed = effective_seed(res.spec)
        assert (d / f"{i}.v1.mid").read_bytes() == compile_spec(spec, seed=seed)  # engine=v1 은 engine 없음과 바이트 동일
        assert (d / f"{i}.v2.mid").read_bytes() == compile_spec(res.spec.model_copy(update={"engine": "v2"}), seed=seed)
        differs += (d / f"{i}.v1.mid").read_bytes() != (d / f"{i}.v2.mid").read_bytes()
    assert differs >= 1, "v2 가 어떤 곡도 바꾸지 않는다면 이 비교는 의미가 없다"


def test_default_set_runs_all_baseline_prompts(tmp_path):
    rc, text, d = run(tmp_path, ids=None)
    n = len(json.loads(SET.read_text(encoding="utf-8"))["prompts"])
    assert rc == 0 and f"{n}/{n} 쌍" in text
    assert len(list(d.glob("*.v1.mid"))) == n == len(list(d.glob("*.v2.mid")))


def test_unknown_id_and_bad_set_and_holdout_are_refused(tmp_path):
    rc, text, d = run(tmp_path, ids="r01,nope")
    assert rc == 2 and "nope" in text and not d.exists()
    rc, text, _ = run(tmp_path, "x", "--set", str(tmp_path / "missing.json"), ids=None)
    assert rc == 2 and "읽지 못했습니다" in text
    holdout = tmp_path / "holdout_set.json"
    holdout.write_text(SET.read_text(encoding="utf-8"), encoding="utf-8")
    rc, text, d2 = run(tmp_path, "h", "--set", str(holdout), ids=None)
    assert rc == 2 and "보류" in text and not d2.exists()


def test_cell_helpers():
    assert cs.pair_cell("out", "in") == "out→in" and cs.pair_cell("in", "in") == "in"
    assert cs.fmt(None) == "—" and cs.fmt(0.5) == "0.5"


def test_out_dir_is_git_ignored():
    assert "out/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
