"""tools/listening_pack.py: 결정적 배정, 반반, key.csv 와 폴더 내용 일치, 평가자 폴더에 v1/v2 비노출. 네트워크·키 없음."""
import csv
import io
import json
import re
import sys
from pathlib import Path

import mido
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import listening_pack as lp  # noqa: E402

from prompt2midi.compiler import compile_spec  # noqa: E402
from prompt2midi.pipeline import generate  # noqa: E402
from prompt2midi.providers import RuleProvider  # noqa: E402
from prompt2midi.spec import effective_seed  # noqa: E402

PROMPTS = {
    "r01": "A calm and relaxing piano piece in C major, 4/4, about 70 BPM.",
    "r03": "A cheerful and upbeat pop song with acoustic guitar, bass and drums in G major, 120 BPM.",
    "r05": "A dark and mysterious cinematic piece for strings in D minor, 80 BPM.",
    "r08": "A romantic and tender ballad for piano and violin in B-flat major, 3/4 time, 90 BPM.",
    "r09": "A playful electronic dance track with synth lead, synth bass and drums in C major, 132 BPM.",
    "e03": "Late-night lo-fi study beats on electric piano, relaxed, 78 BPM",
    "e06": "Bossa nova tune with nylon guitar and flute in A minor at 120 BPM",
    "e09": "Blues shuffle with organ and electric guitar in E major at 92 BPM",
}


@pytest.fixture(scope="module")
def pairs_dir(tmp_path_factory):
    """compare_samples 와 같은 방식으로 만든 진짜 v1/v2 쌍(엔진만 다르고 seed 는 같음)."""
    d = tmp_path_factory.mktemp("pairs")
    for pid, text in PROMPTS.items():
        res = generate(text, provider=RuleProvider(), fallback=False)
        seed = effective_seed(res.spec)
        for eng in ("v1", "v2"):
            (d / f"{pid}.{eng}.mid").write_bytes(compile_spec(res.spec.model_copy(update={"engine": eng}), seed=seed))
    return d


def run(pairs, out, seed=7, *extra):
    buf = io.StringIO()
    rc = lp.main(["--pairs", str(pairs), "--out", str(out), "--seed", str(seed), *extra], out=buf)
    return rc, buf.getvalue()


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def read_key(out: Path):
    return list(csv.DictReader(open(out / "answer_key/key.csv", encoding="utf-8")))


def test_assignment_is_deterministic_and_seed_dependent(tmp_path, pairs_dir):
    assert run(pairs_dir, tmp_path / "a", 7)[0] == 0
    assert run(pairs_dir, tmp_path / "b", 7)[0] == 0
    assert tree(tmp_path / "a") == tree(tmp_path / "b")  # 같은 입력·같은 시드 → 바이트 동일
    run(pairs_dir, tmp_path / "c", 8)
    assert read_key(tmp_path / "a") != read_key(tmp_path / "c")  # 시드가 다르면 배정이 달라진다


GOLDEN = [("p01", "d", "v2", "v1"), ("p02", "a", "v1", "v2"), ("p03", "c", "v1", "v2"), ("p04", "b", "v2", "v1")]


def test_assign_golden_values_do_not_depend_on_python_version():
    assert lp.assign(["a", "b", "c", "d", "e"], 7) == lp.assign(["e", "d", "c", "b", "a"], 7)  # 입력 순서와 무관
    # 해시 기반이라 값이 고정이다(파이썬·플랫폼이 바뀌어도 같아야 한다)
    assert [(g["piece"], g["id"], g["a"], g["b"]) for g in lp.assign(["a", "b", "c", "d"], 1)] == GOLDEN


def test_about_half_each(tmp_path, pairs_dir):
    run(pairs_dir, tmp_path / "o", 123)
    rows = read_key(tmp_path / "o")
    a_v1 = sum(r["a_engine"] == "v1" for r in rows)
    assert len(rows) == len(PROMPTS) and abs(a_v1 - (len(rows) - a_v1)) <= 1
    for seed in range(40):  # 어떤 시드로도 반반(홀수면 한 곡 차이)
        got = lp.assign([f"x{i}" for i in range(11)], seed)
        n = sum(g["a"] == "v1" for g in got)
        assert n in (5, 6)
        assert all(g["a"] != g["b"] for g in got)


def test_pieces_are_shuffled_not_in_id_order(tmp_path, pairs_dir):
    orders = set()
    for seed in range(6):
        run(pairs_dir, tmp_path / f"o{seed}", seed)
        orders.add(tuple(r["original_id"] for r in read_key(tmp_path / f"o{seed}")))
    assert len(orders) > 1  # 시드에 따라 곡 순서가 바뀐다


def test_key_matches_folder_contents(tmp_path, pairs_dir):
    run(pairs_dir, tmp_path / "o", 99)
    rows = read_key(tmp_path / "o")
    assert sorted(r["original_id"] for r in rows) == sorted(PROMPTS)
    for r in rows:
        a = (tmp_path / "o/for_raters" / r["piece"] / "A.mid").read_bytes()
        b = (tmp_path / "o/for_raters" / r["piece"] / "B.mid").read_bytes()
        assert a == (pairs_dir / f"{r['original_id']}.{r['a_engine']}.mid").read_bytes()
        assert b == (pairs_dir / f"{r['original_id']}.{r['b_engine']}.mid").read_bytes()
        assert {r["a_engine"], r["b_engine"]} == {"v1", "v2"} and a != b
        assert r["a_sha8"] and r["b_sha8"] and r["a_sha8"] != r["b_sha8"]


def test_rater_folder_never_shows_v1_v2_or_engine(tmp_path, pairs_dir):
    run(pairs_dir, tmp_path / "o", 987654)
    rater = tmp_path / "o/for_raters"
    word = re.compile(r"v1|v2|engine|엔진|baseline|compare", re.I)
    files = [p for p in rater.rglob("*") if p.is_file()]
    for p in files:
        assert not word.search(str(p.relative_to(rater))), p  # 파일·폴더 이름
        if p.suffix != ".mid":
            text = p.read_text(encoding="utf-8")
            assert not word.search(text), p  # 문서·점수표 내용
            assert "987654" not in text  # 시드도 비공개
        else:
            m = mido.MidiFile(file=io.BytesIO(p.read_bytes()))  # MIDI 안의 이름·텍스트 이벤트
            for tr in m.tracks:
                for msg in tr:
                    for fld in ("name", "text"):
                        assert not word.search(str(getattr(msg, fld, ""))), (p, msg)
    # 원래 곡 id 도 나오지 않는다(곡 번호 p01… 만)
    for pid in PROMPTS:
        assert not any(pid in str(p.relative_to(rater)) for p in files)
    # 들어 있는 파일은 정확히 이것뿐
    pieces = sorted(d.name for d in rater.iterdir() if d.is_dir())
    assert pieces == [f"p{i:02d}" for i in range(1, len(PROMPTS) + 1)]
    assert sorted(p.name for p in rater.iterdir() if p.is_file()) == ["README.md", "score_sheet.csv"]
    for d in pieces:
        assert sorted(p.name for p in (rater / d).iterdir()) == ["A.mid", "B.mid"]


def test_answer_key_is_not_in_the_rater_folder(tmp_path, pairs_dir):
    run(pairs_dir, tmp_path / "o", 5)
    assert not list((tmp_path / "o/for_raters").rglob("key.csv"))
    assert sorted(p.name for p in (tmp_path / "o/answer_key").iterdir()) == ["README.md", "excluded.csv", "key.csv", "pack_info.json"]
    assert "평가자에게 주지 마세요" in (tmp_path / "o/answer_key/README.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "o/answer_key/pack_info.json").read_text(encoding="utf-8"))["seed"] == 5


def test_score_sheet_template(tmp_path, pairs_dir):
    run(pairs_dir, tmp_path / "o", 5)
    rows = list(csv.reader(open(tmp_path / "o/for_raters/score_sheet.csv", encoding="utf-8")))
    assert rows[0] == ["곡", "선호(A/B/동일)", "메모"]
    assert [r[0] for r in rows[1:]] == [f"p{i:02d}" for i in range(1, len(PROMPTS) + 1)]
    assert all(r[1:] == ["", ""] for r in rows[1:])


def test_identical_pairs_are_excluded_by_default(tmp_path, pairs_dir):
    d = tmp_path / "pairs"
    d.mkdir()
    for p in pairs_dir.glob("r0[135].*.mid"):
        (d / p.name).write_bytes(p.read_bytes())
    same = (pairs_dir / "r01.v1.mid").read_bytes()
    (d / "zz.v1.mid").write_bytes(same)
    (d / "zz.v2.mid").write_bytes(same)  # 바이트 동일
    (d / "lonely.v1.mid").write_bytes(same)  # 짝 없음
    (d / "notes.txt").write_text("x", encoding="utf-8")
    assert run(d, tmp_path / "o", 3)[0] == 0
    rows = read_key(tmp_path / "o")
    assert sorted(r["original_id"] for r in rows) == ["r01", "r03", "r05"]
    info = json.loads((tmp_path / "o/answer_key/pack_info.json").read_text(encoding="utf-8"))
    assert info["excluded_identical"] == ["zz"] and info["unpaired_files_ignored"] == ["lonely.v1.mid"]
    assert "zz" in (tmp_path / "o/answer_key/excluded.csv").read_text(encoding="utf-8")
    assert run(d, tmp_path / "p", 3, "--include-identical")[0] == 0
    assert sorted(r["original_id"] for r in read_key(tmp_path / "p")) == ["r01", "r03", "r05", "zz"]


def test_errors(tmp_path, pairs_dir):
    assert run(tmp_path / "nope", tmp_path / "o")[0] == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, text = run(empty, tmp_path / "o2")
    assert rc == 2 and "묶을 쌍이 없습니다" in text
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "q.v1.mid").write_bytes(b"not midi")
    (bad / "q.v2.mid").write_bytes(b"also not")
    rc, text = run(bad, tmp_path / "o3")
    assert rc == 2 and "MIDI 파일이 아닙니다" in text
    with pytest.raises(SystemExit):  # --seed 는 필수(암묵적인 기본 시드 없음)
        lp.main(["--pairs", str(pairs_dir)], out=io.StringIO())


def test_rerun_replaces_old_output(tmp_path, pairs_dir):
    out = tmp_path / "o"
    run(pairs_dir, out, 1)
    (out / "for_raters/stale.txt").write_text("old", encoding="utf-8")
    (out / "answer_key/stale.txt").write_text("old", encoding="utf-8")
    run(pairs_dir, out, 1)
    assert not (out / "for_raters/stale.txt").exists() and not (out / "answer_key/stale.txt").exists()


def test_works_on_compare_samples_output_if_present(tmp_path):
    """tools/compare_samples.py 가 있으면 그 출력 폴더를 그대로 입력으로 받을 수 있다."""
    if not (ROOT / "tools/compare_samples.py").exists():
        pytest.skip("compare_samples.py 가 아직 없음")
    import compare_samples as cs
    assert cs.main(["--out", str(tmp_path / "cmp"), "--commit", "t", "--ids", "r01,r03,r08,s07"], out=io.StringIO()) == 0
    rc, text = run(tmp_path / "cmp", tmp_path / "pack", 11)
    assert rc == 0 and "4곡" in text
