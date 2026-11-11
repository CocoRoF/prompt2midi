"""tools/live_eval.py: 드라이런·플래그·키 처리·단계·호출 상한·키 비노출. 모두 가짜 어댑터로(네트워크·비용 0)."""
import copy
import io
import json
import logging
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import live_eval  # noqa: E402

from prompt2midi.api.config import AppSettings  # noqa: E402
from prompt2midi.api.store import Store  # noqa: E402
from prompt2midi.evalkit import load_set, run_set  # noqa: E402
from prompt2midi.evalkit import __main__ as evalkit_cli  # noqa: E402
from prompt2midi.evalkit.report import rows  # noqa: E402
from prompt2midi.evalkit.runner import FAKE_SPEC  # noqa: E402
from prompt2midi.providers.adapters import AdapterResult, AnthropicSDKAdapter, OpenAISDKAdapter  # noqa: E402

CLOCK = lambda: 1_800_000_000.0  # noqa: E731  (고정 시각: 하루 상한 날짜가 흔들리지 않게)


class FakeAdapter:
    """valid 사양을 돌려주는 가짜. 호출 수를 센다."""

    def __init__(self, text=None, boom=None):
        self.calls = 0
        self.text = text if text is not None else json.dumps(FAKE_SPEC)
        self.boom = boom

    def complete_json(self, *, model, api_key, system, user, schema, max_tokens, timeout):
        self.calls += 1
        if self.boom:
            raise RuntimeError(self.boom(api_key))
        return AdapterResult(self.text, "end")


@pytest.fixture(autouse=True)
def no_real_sdk(monkeypatch):
    """진짜 SDK 어댑터가 불리면 시험을 실패시킨다(공급자가 예외를 삼켜도 잡히도록 기록해 두었다가 확인)."""
    attempted = []

    def trap(self, **kw):
        attempted.append(1)
        raise AssertionError("real SDK adapter was called in a test")

    monkeypatch.setattr(AnthropicSDKAdapter, "complete_json", trap)
    monkeypatch.setattr(OpenAISDKAdapter, "complete_json", trap)
    monkeypatch.delenv("P2M_LIVE", raising=False)
    monkeypatch.delenv("P2M_DATA_DIR", raising=False)
    monkeypatch.delenv("P2M_SECRET_KEY", raising=False)
    yield
    assert attempted == [], "a real SDK call was attempted"


def make_set(tmp_path, n=25, name="live-eval test set") -> Path:
    base = json.loads((ROOT / "tests/fixtures/smoke_set.json").read_text(encoding="utf-8"))
    prompts = []
    for i in range(n):
        p = copy.deepcopy(base["prompts"][i % len(base["prompts"])])
        p["id"] = f"t{i + 1:02d}"
        prompts.append(p)
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"version": "1", "name": name, "prompts": prompts}, ensure_ascii=False), encoding="utf-8")
    return path


def make_data(tmp_path, *, key="auto", model="test-model", enabled=True, daily_cap=100, max_retries=2, secret_key=None):
    d = tmp_path / "data"
    cfg = AppSettings(data_dir=d, secret_key=secret_key)
    store = Store(cfg)
    k = ("k" + secrets.token_hex(20)) if key == "auto" else key
    if k:
        store.set_key("anthropic", k)

    def setup(data):
        data["providers"]["anthropic"].update({"model": model, "enabled": enabled})
        data["limits"].update({"daily_llm_cap": daily_cap, "max_retries": max_retries})

    store.update_settings(setup)
    return d, k


def run(args, adapter=None, capsys=None):
    buf = io.StringIO()
    rc = live_eval.main(args, adapter=adapter, clock=CLOCK, out=buf)
    return rc, buf.getvalue()


def base_args(tmp_path, set_path, data_dir, stage="5", *extra):
    return [str(set_path), "--provider", "anthropic", "--stage", stage, "--data-dir", str(data_dir), "--out", str(tmp_path / "out"), *extra]


# ---------------- 드라이런·플래그 ----------------
def test_dry_run_is_default_and_makes_zero_calls(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, key = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")  # 환경변수가 있어도 플래그가 없으면 호출 없음
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d), ad)
    assert rc == 0 and ad.calls == 0
    assert "드라이런(호출 0)" in text and "최소 5회" in text and "최대 15회" in text
    assert "호출하지 않았습니다" in text
    assert key not in text and not (tmp_path / "out").exists()
    assert Store(AppSettings(data_dir=d)).usage_today("2027-01-15") == 0


@pytest.mark.parametrize("extra", [["--yes-spend"], ["--max-calls", "5"]])
def test_one_of_the_two_flags_is_refused(tmp_path, extra):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d, "5", *extra), ad)
    assert rc == 2 and ad.calls == 0 and "둘 다" in text


def test_max_calls_must_be_positive(tmp_path):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    rc, text = run(base_args(tmp_path, sp, d, "5", "--yes-spend", "--max-calls", "0"), FakeAdapter())
    assert rc == 2 and "1 이상" in text


def test_no_key_means_not_run_with_exit_zero(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, key=None)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d, "5", "--yes-spend", "--max-calls", "10"), ad)
    assert rc == 0 and ad.calls == 0 and "미실행(키 없음)" in text and not (tmp_path / "out").exists()


def test_missing_data_dir_is_not_created(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    missing = tmp_path / "nope"
    rc, text = run(base_args(tmp_path, sp, missing, "5", "--yes-spend", "--max-calls", "10"), FakeAdapter())
    assert rc == 0 and "미실행(키 없음)" in text and not missing.exists()


def test_encrypted_secrets_without_secret_key_means_no_key(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, secret_key=secrets.token_hex(16))  # 암호화해서 저장, 이 프로세스에는 P2M_SECRET_KEY 없음
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d, "5", "--yes-spend", "--max-calls", "10"), ad)
    assert rc == 0 and ad.calls == 0 and "미실행(키 없음)" in text


@pytest.mark.parametrize("kw,word", [({"model": ""}, "모델 미설정"), ({"enabled": False}, "사용 꺼짐")])
def test_model_and_enabled_are_required(tmp_path, monkeypatch, kw, word):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, **kw)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d, "5", "--yes-spend", "--max-calls", "10"), ad)
    assert rc == 0 and ad.calls == 0 and f"미실행({word})" in text


def test_p2m_live_env_is_required(tmp_path):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d, "5", "--yes-spend", "--max-calls", "10"), ad)
    assert rc == 0 and ad.calls == 0 and "P2M_LIVE=1 아님" in text and not (tmp_path / "out").exists()


def test_holdout_named_sets_are_refused(tmp_path):
    sp = make_set(tmp_path, name="holdout-20")
    d, _ = make_data(tmp_path)
    ad = FakeAdapter()
    rc, text = run(base_args(tmp_path, sp, d), ad)
    assert rc == 2 and "보류(holdout)" in text and ad.calls == 0
    renamed = tmp_path / "보류_세트.json"
    renamed.write_text(make_set(tmp_path).read_text(encoding="utf-8"), encoding="utf-8")
    rc, _ = run(base_args(tmp_path, renamed, d), ad)
    assert rc == 2


# ---------------- 실제(가짜 어댑터) 실행 ----------------
def live_args(tmp_path, sp, d, stage="5", calls=50, *extra):
    return base_args(tmp_path, sp, d, stage, "--yes-spend", "--max-calls", str(calls), *extra)


def test_live_run_writes_evalkit_files_and_counts_usage(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, key = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(live_args(tmp_path, sp, d), ad)
    assert rc == 0 and ad.calls == 5, text
    out = tmp_path / "out" / "anthropic" / "stage-5"
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "responses.jsonl", "results.csv", "set.json", "summary.md"]
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "완료" and m["llm_calls"] == 5 and m["completed_prompts"] == 5 and m["prompt_ids"] == ["t01", "t02", "t03", "t04", "t05"]
    assert m["model"] == "test-model" and m["fallback"] is False
    assert Store(AppSettings(data_dir=d)).usage_today("2027-01-15") == 5  # 서버와 같은 usage.json 에 반영
    # evalkit 으로 이어진다: 같은 세트 파일을 evalkit 이 읽고 규칙 공급자로 돌릴 수 있다
    es, _ = load_set(out / "set.json")
    assert [p.id for p in es.prompts] == m["prompt_ids"]
    assert evalkit_cli.main([str(out / "set.json"), "--provider", "rule", "--out", str(tmp_path / "ek")]) == 0
    assert (tmp_path / "ek" / "results.csv").exists()
    # 같은 열 구성
    header = (out / "results.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == (tmp_path / "ek" / "results.csv").read_text(encoding="utf-8").splitlines()[0]
    assert key not in text


def test_scoring_matches_evalkit_run_set(tmp_path, monkeypatch):
    """live_eval 의 채점은 evalkit.run_set 과 같아야 한다(복제한 절차가 어긋나지 않게)."""
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    rc, _ = run(live_args(tmp_path, sp, d), FakeAdapter())
    assert rc == 0
    out = tmp_path / "out" / "anthropic" / "stage-5"
    sub, _ = load_set(out / "set.json")
    ref = {r["id"]: r for r in rows(run_set(sub, "fake", check_determinism=False))}
    import csv
    got = {r["id"]: r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8"))}
    assert set(got) == set(ref)
    skip = {"provider", "provider_used"}  # 공급자 이름만 다르다
    for i in ref:
        for col, v in ref[i].items():
            if col in skip:
                continue
            assert str(got[i][col]) == str(v), (i, col)


def test_max_calls_stops_the_run_and_is_recorded(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(live_args(tmp_path, sp, d, "5", 3), ad)
    assert rc == 0 and ad.calls == 3 and "중단" in text
    m = json.loads((tmp_path / "out/anthropic/stage-5/manifest.json").read_text(encoding="utf-8"))
    assert m["status"].startswith("중단") and m["completed_prompts"] == 3 and m["llm_calls"] == 3


def test_retries_count_toward_max_calls(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, max_retries=2)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter(text="not json")  # 항상 실패 → 항목당 3회 시도
    rc, text = run(live_args(tmp_path, sp, d, "5", 4), ad)
    assert rc == 0 and ad.calls == 4, text  # 4회에서 멈춘다(두 번째 항목 도중)
    assert "중단" in text


def test_daily_cap_is_enforced(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, daily_cap=2)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(live_args(tmp_path, sp, d, "5", 50), ad)
    assert rc == 0 and ad.calls == 2 and "하루 상한" in text
    # 다시 실행하면 소진되어 미실행
    ad2 = FakeAdapter()
    rc, text = run(live_args(tmp_path, sp, d, "5", 50, "--rerun"), ad2)
    assert rc == 0 and ad2.calls == 0 and "하루 상한 소진" in text


def test_daily_usage_already_at_cap_means_not_run(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path, daily_cap=5)
    Store(AppSettings(data_dir=d)).add_usage("2027-01-15", 5)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    rc, text = run(live_args(tmp_path, sp, d), ad)
    assert rc == 0 and ad.calls == 0 and "미실행(하루 상한 소진)" in text


# ---------------- 단계 ----------------
def test_stage_order_and_human_review_gate(tmp_path, monkeypatch):
    sp = make_set(tmp_path, n=25)
    d, _ = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    # 5 를 건너뛰고 20 → 거절
    rc, text = run(live_args(tmp_path, sp, d, "20"), ad)
    assert rc == 2 and ad.calls == 0 and "단계 5" in text
    # 드라이런도 같은 이유로 알려 준다
    rc, text = run(base_args(tmp_path, sp, d, "20"), ad)
    assert rc == 2 and ad.calls == 0
    # 5 완료
    assert run(live_args(tmp_path, sp, d, "5"), ad)[0] == 0 and ad.calls == 5
    # 사람 확인(REVIEWED) 없이 20 → 거절
    rc, text = run(live_args(tmp_path, sp, d, "20"), ad)
    assert rc == 2 and "REVIEWED" in text and ad.calls == 5
    # 이미 완료한 단계는 --rerun 없이 거절
    rc, text = run(live_args(tmp_path, sp, d, "5"), ad)
    assert rc == 2 and "이미 완료" in text and ad.calls == 5
    (tmp_path / "out/anthropic/stage-5/REVIEWED").write_text("ok", encoding="utf-8")
    rc, text = run(live_args(tmp_path, sp, d, "20"), ad)
    assert rc == 0 and ad.calls == 5 + 15
    m = json.loads((tmp_path / "out/anthropic/stage-20/manifest.json").read_text(encoding="utf-8"))
    assert m["prompt_ids"][0] == "t06" and m["prompt_ids"][-1] == "t20"  # 겹치지 않는 구간
    # 20 → all 도 같은 규칙, all 은 21번째부터
    rc, _ = run(live_args(tmp_path, sp, d, "all"), ad)
    assert rc == 2
    (tmp_path / "out/anthropic/stage-20/REVIEWED").write_text("ok", encoding="utf-8")
    rc, _ = run(live_args(tmp_path, sp, d, "all"), ad)
    assert rc == 0 and ad.calls == 25
    m = json.loads((tmp_path / "out/anthropic/stage-all/manifest.json").read_text(encoding="utf-8"))
    assert m["prompt_ids"] == ["t21", "t22", "t23", "t24", "t25"]


def test_gate_rejects_a_different_set(tmp_path, monkeypatch):
    sp = make_set(tmp_path, n=25)
    d, _ = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    run(live_args(tmp_path, sp, d, "5"), ad)
    (tmp_path / "out/anthropic/stage-5/REVIEWED").write_text("ok", encoding="utf-8")
    (tmp_path / "other").mkdir()
    other = make_set(tmp_path / "other", n=25, name="another set")
    rc, text = run(live_args(tmp_path, other, d, "20"), ad)
    assert rc == 2 and "다른 세트" in text


def test_empty_stage_is_an_error(tmp_path):
    sp = make_set(tmp_path, n=12)
    d, _ = make_data(tmp_path)
    rc, text = run(base_args(tmp_path, sp, d, "all"), FakeAdapter())
    assert rc == 2 and "비어 있습니다" in text


def test_rerun_invalidates_review_mark(tmp_path, monkeypatch):
    sp = make_set(tmp_path)
    d, _ = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    ad = FakeAdapter()
    run(live_args(tmp_path, sp, d, "5"), ad)
    rev = tmp_path / "out/anthropic/stage-5/REVIEWED"
    rev.write_text("ok", encoding="utf-8")
    run(live_args(tmp_path, sp, d, "5", 50, "--rerun"), ad)
    assert not rev.exists()


# ---------------- 키 비노출 ----------------
def everything_written(tmp_path, d):
    out = tmp_path / "out"
    texts = []
    for root in (out, d / "usage.json"):
        paths = [root] if root.is_file() else list(root.rglob("*")) if root.exists() else []
        for p in paths:
            if p.is_file():
                texts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def test_key_never_appears_even_when_the_sdk_error_echoes_it(tmp_path, monkeypatch, capsys, caplog):
    sp = make_set(tmp_path)
    d, key = make_data(tmp_path)
    monkeypatch.setenv("P2M_LIVE", "1")
    caplog.set_level(logging.DEBUG)
    ad = FakeAdapter(boom=lambda k: f"401 Incorrect API key provided: {k[:12]}...{k[-6:]} (full {k}) x-api-key: {k}")
    rc, text = run(live_args(tmp_path, sp, d, "5", 50), ad)
    assert rc == 0 and ad.calls == 15  # 5 프롬프트 × (1+2 재시도), 전부 실패
    seen = text + capsys.readouterr().out + capsys.readouterr().err + caplog.text + everything_written(tmp_path, d)
    assert key not in seen
    for frag in (key[:8], key[-8:], key[10:18]):  # 키 조각(8자)도 없어야 한다
        assert frag not in seen
    assert "[REDACTED]" in seen  # 오류 문구는 가려진 채로 남는다


def test_dry_run_output_has_no_key_hint(tmp_path):
    sp = make_set(tmp_path)
    d, key = make_data(tmp_path)
    rc, text = run(base_args(tmp_path, sp, d), FakeAdapter())
    assert key not in text and key[-4:] not in text and "…" + key[-4:] not in text


def test_script_runs_as_a_command_dry_run(tmp_path):
    import subprocess
    sp = make_set(tmp_path)
    d, key = make_data(tmp_path)
    r = subprocess.run([sys.executable, str(ROOT / "tools/live_eval.py"), *base_args(tmp_path, sp, d)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "드라이런(호출 0)" in r.stdout and key not in r.stdout + r.stderr
