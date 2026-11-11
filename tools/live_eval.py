#!/usr/bin/env python3
"""실제 LLM 평가 준비물: 평가 세트를 단계(5 → 20 → 나머지)로 나눠 LLM 공급자로 생성하고 evalkit 과 같은 형식으로 채점한다.

기본은 **드라이런**이다. 네트워크 호출·비용이 0이고, 보낼 요청 수와 최대 호출 수만 출력한다.
실제 호출은 아래가 **모두** 있을 때만 일어난다(하나라도 빠지면 호출 0):
  1) `--yes-spend` 와 `--max-calls N` 둘 다   2) 환경변수 `P2M_LIVE=1`   3) 관리자 패널에 등록된 키·모델·"사용" 켜짐
  4) 하루 상한(관리자 설정 daily_llm_cap)이 남아 있음   5) 앞 단계가 끝났고 사람이 확인했음(REVIEWED 파일)

사용:
  python tools/live_eval.py SET.json --provider anthropic --stage 5                 # 드라이런
  P2M_LIVE=1 python tools/live_eval.py SET.json --provider anthropic --stage 5 --yes-spend --max-calls 15

키는 앱의 키 저장소(`P2M_DATA_DIR` 의 secrets.json, 관리자 패널에서 등록)에서만 읽는다. 환경변수·인자·파일로 받지 않고,
출력·로그·예외·결과 파일 어디에도 남기지 않는다. 사람이 할 일의 순서는 docs/live-eval-checklist.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from prompt2midi.api.config import AppSettings  # noqa: E402
from prompt2midi.api.store import Store  # noqa: E402
from prompt2midi.evalkit import EvalItem, EvalSet, SetError, load_set  # noqa: E402
from prompt2midi.evalkit.metrics import METRICS, Context, midicheck_valid, note_integrity  # noqa: E402
from prompt2midi.evalkit.proxy import evaluate as evaluate_proxies  # noqa: E402
from prompt2midi.evalkit.report import summary_md, write_csv  # noqa: E402
from prompt2midi.evalkit.runner import ItemResult, labels_of  # noqa: E402
from prompt2midi.evalkit.version import EVALKIT_VERSION  # noqa: E402
from prompt2midi.pipeline import GenerationError, generate  # noqa: E402
from prompt2midi.providers import LLM_IDS, build_provider, mask_secrets  # noqa: E402
from prompt2midi.spec import total_bars  # noqa: E402

STAGES = ("5", "20", "all")
STAGE_ORDER = {"5": None, "20": "5", "all": "20"}  # 단계 → 먼저 끝나야 하는 단계
NOT_RUN = "미실행"
HOLDOUT_WORDS = ("holdout", "보류")


class BudgetExhausted(BaseException):
    """호출 상한에 닿았다. generate() 의 `except Exception` 에 잡히지 않도록 BaseException 으로 둔다(남은 재시도도 막는다)."""


def stage_slice(prompts: list, stage: str) -> list:
    """겹치지 않는 구간: 5 = 처음 5개, 20 = 6~20번째, all = 21번째부터 끝까지."""
    return {"5": prompts[:5], "20": prompts[5:20], "all": prompts[20:]}[stage]


class BudgetedProvider:
    """공급자를 감싸 호출마다 (1) 이번 실행의 --max-calls (2) 하루 상한을 확인하고 사용량 파일에 1을 더한다. 호출 직전에 센다."""

    def __init__(self, inner, store: Store, day: Callable[[], str], max_calls: int, daily_cap: int):
        self.inner, self.store, self.day, self.max_calls, self.daily_cap = inner, store, day, max_calls, daily_cap
        self.name = inner.name
        self.calls = 0
        self.stopped_by = ""

    def generate_spec(self, prompt, schema, *, feedback=None, seed=None):
        if self.calls >= self.max_calls:
            self.stopped_by = "--max-calls"
            raise BudgetExhausted
        if self.store.usage_today(self.day()) >= self.daily_cap:
            self.stopped_by = "하루 상한(daily_llm_cap)"
            raise BudgetExhausted
        self.store.add_usage(self.day(), 1)  # 센 다음에 부른다: 중간에 실패해도 호출로 친다
        self.calls += 1
        return self.inner.generate_spec(prompt, schema, feedback=feedback, seed=seed)


# ---------------- 도우미 ----------------
def out_dir(root: Path, provider: str, stage: str) -> Path:
    return root / provider / f"stage-{stage}"


def read_manifest(d: Path) -> dict | None:
    try:
        return json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stage_gate(root: Path, provider: str, stage: str, set_sha: str) -> str | None:
    """앞 단계가 안 끝났거나 사람이 확인하지 않았으면 이유를, 통과면 None."""
    prev = STAGE_ORDER[stage]
    if prev is None:
        return None
    d = out_dir(root, provider, prev)
    m = read_manifest(d)
    if not m or m.get("status") != "완료":
        return f"단계 {prev} 가 아직 '완료'가 아닙니다({d}). 단계는 5 → 20 → all 순서로만 진행합니다."
    if m.get("set_sha256") != set_sha:
        return f"단계 {prev} 는 다른 세트(sha256 앞 8자 {str(m.get('set_sha256'))[:8]})로 돌렸습니다. 같은 세트로 처음부터 다시 하세요."
    if not (d / "REVIEWED").exists():
        return f"단계 {prev} 결과를 사람이 확인했다는 표시 파일이 없습니다. 확인한 뒤 `touch {d / 'REVIEWED'}` (docs/live-eval-checklist.md)."
    return None


def credentials(cfg: AppSettings, provider: str) -> tuple[Store | None, str]:
    """(저장소, 상태). 상태: ok | 키 없음 | 모델 미설정 | 사용 꺼짐. 키 값은 확인만 하고 내보내지 않는다."""
    if not Path(cfg.data_dir).is_dir():  # Store() 는 디렉터리를 만들기 때문에 없으면 건드리지 않는다
        return None, "키 없음"
    store = Store(cfg)
    if not store.get_key(provider):
        return store, "키 없음"
    conf = store.settings()["providers"][provider]
    if not str(conf.get("model") or "").strip():
        return store, "모델 미설정"
    if not conf.get("enabled"):
        return store, "사용 꺼짐"
    return store, "ok"


def sub_set(eval_set: EvalSet, items: list[EvalItem], stage: str) -> EvalSet:
    return EvalSet(version="1", name=f"{eval_set.name} [live-eval stage {stage}]", prompts=items)


def write_set(d: Path, es: EvalSet) -> None:
    (d / "set.json").write_text(json.dumps(es.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def score_item(item: EvalItem, provider: str, provider_obj, max_retries: int, key: str, tempo_tol: float | None) -> tuple[ItemResult, dict]:
    """evalkit.runner.run_set 의 한 항목과 같은 절차(공급자만 측정: 폴백 끔). 결정성 검사는 호출이 두 배가 되므로 하지 않는다."""
    spec = midi = None
    err, used, attempts, fb = "", provider, 0, False
    try:
        res = generate(item.text, provider=provider_obj, fallback=False, max_retries=max_retries)
        spec, midi, used, attempts, fb = res.spec, res.midi, res.provider_used, len(res.attempts), res.fallback_used
    except GenerationError as exc:
        attempts, err = len(exc.attempts), mask_secrets(str(exc), [key])[:300]
    except ValueError as exc:
        err = mask_secrets(str(exc), [key])[:300]
    ctx = Context(item.expect, spec, midi, err, None, tempo_tol)
    result = ItemResult(
        item.id, item.lang, item.tier, midi is not None, provider, labels_of(item), err, used if midi is not None else "", attempts, fb,
        total_bars(spec) if spec is not None else None, hashlib.sha256(midi).hexdigest()[:8] if midi else "",
        {m.name: m.fn(ctx) for m in METRICS},
        evaluate_proxies(midi, spec, item.expect, midi is not None and midicheck_valid(ctx).status != "fail" and note_integrity(ctx).status != "fail"),  # same rule as run_set
    )
    record = {"id": item.id, "generated": midi is not None, "attempts": attempts, "error": err,
              "spec": spec.model_dump(mode="json", by_alias=True) if spec is not None else None}
    return result, record


# ---------------- 본체 ----------------
def main(argv: list[str] | None = None, *, adapter=None, clock: Callable[[], float] = time.time, out=None) -> int:
    out = out or sys.stdout
    say = lambda s="": print(s, file=out)  # noqa: E731
    ap = argparse.ArgumentParser(prog="tools/live_eval.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", help="평가 세트 JSON 경로(evalkit 형식). 보류(holdout) 세트는 받지 않는다")
    ap.add_argument("--provider", choices=LLM_IDS, required=True)
    ap.add_argument("--stage", choices=STAGES, required=True, help="5 = 처음 5개, 20 = 6~20번째, all = 21번째부터 끝까지 (겹치지 않음)")
    ap.add_argument("--data-dir", default=None, help="키·설정 저장소(기본 $P2M_DATA_DIR 또는 ./instance). 관리자 패널이 쓰는 곳")
    ap.add_argument("--out", default="out/live-eval", help="결과 폴더 뿌리(기본 out/live-eval). <뿌리>/<공급자>/stage-<단계>/ 에 쓴다")
    ap.add_argument("--max-retries", type=int, default=None, help="재시도 횟수(기본: 관리자 설정 limits.max_retries)")
    ap.add_argument("--tempo-tol", type=float, default=None, metavar="RATIO", help="evalkit 과 같은 템포 허용오차 비율(기본 끔)")
    ap.add_argument("--yes-spend", action="store_true", help="실제 호출(비용 발생)을 허용한다. --max-calls 와 함께만 유효")
    ap.add_argument("--max-calls", type=int, default=None, metavar="N", help="이번 실행에서 보낼 LLM 호출의 최대 수(재시도 포함)")
    ap.add_argument("--rerun", action="store_true", help="이미 '완료'된 단계를 다시 돌린다(다시 비용이 든다)")
    ap.add_argument("--holdout", action="store_true", help="보류 세트 이름·경로를 허용한다. 연구 1팀 합의와 사람의 결정이 있을 때만")
    args = ap.parse_args(argv)

    # --- 플래그 검사 (호출 전에 모두) ---
    if args.yes_spend != (args.max_calls is not None):
        say("오류: 실제 호출은 --yes-spend 와 --max-calls N 이 둘 다 있어야 합니다(하나만 주면 아무것도 하지 않습니다).")
        return 2
    if args.max_calls is not None and args.max_calls < 1:
        say("오류: --max-calls 는 1 이상이어야 합니다.")
        return 2
    if args.max_retries is not None and not 0 <= args.max_retries <= 5:
        say("오류: --max-retries 는 0~5 입니다.")
        return 2
    if args.tempo_tol is not None and not 0 < args.tempo_tol <= 1:
        say("오류: --tempo-tol 은 0 초과 1 이하의 비율입니다.")
        return 2
    live = args.yes_spend

    # --- 세트 ---
    try:
        eval_set, set_sha = load_set(args.set)
    except (SetError, OSError) as exc:
        say(f"세트를 읽지 못했습니다: {exc}")
        return 2
    if not args.holdout and any(w in (args.set + " " + eval_set.name).lower() for w in HOLDOUT_WORDS):
        say("오류: 보류(holdout) 세트로 보입니다(이름·경로). 개발 1팀은 보류 세트를 열지 않습니다. 사람이 결정했다면 --holdout.")
        return 2
    items = stage_slice(eval_set.prompts, args.stage)
    if not items:
        say(f"오류: 단계 {args.stage} 구간이 비어 있습니다(세트 {len(eval_set.prompts)}개).")
        return 2

    root = Path(args.out)
    d = out_dir(root, args.provider, args.stage)
    existing = read_manifest(d)
    if existing and existing.get("status") == "완료" and not args.rerun:
        say(f"오류: 단계 {args.stage} 는 이미 완료됐습니다({d}). 다시 돌리려면 --rerun(다시 비용이 듭니다).")
        return 2
    gate = stage_gate(root, args.provider, args.stage, set_sha)
    if gate:
        say(f"단계 진행 불가: {gate}")
        return 2

    # --- 저장소·설정 (키 값은 읽지도 출력하지도 않고 있는지만 본다) ---
    cfg = AppSettings.from_env()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir)
    cfg.clock = clock
    store, cred = credentials(cfg, args.provider)
    limits = store.settings()["limits"] if store else {"max_retries": 2, "daily_llm_cap": 100}
    max_retries = args.max_retries if args.max_retries is not None else limits["max_retries"]
    day = datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%d")
    used_today = store.usage_today(day) if store else 0
    daily_cap = limits["daily_llm_cap"]
    n = len(items)
    worst = n * (max_retries + 1)

    say(f"# live_eval — {'실제 호출' if live else '드라이런(호출 0)'}")
    say(f"- 세트: {eval_set.name} ({len(eval_set.prompts)}개, sha256 앞 8자 {set_sha[:8]}) · 공급자: {args.provider} · 단계: {args.stage} (이번 구간 {n}개: {items[0].id} … {items[-1].id})")
    say(f"- 보낼 요청: 최소 {n}회(프롬프트 1개당 1회, 재시도 없을 때) · 최대 {worst}회(재시도 {max_retries}회까지 전부 쓸 때) · 폴백 끔(공급자만 측정) · 결정성 재생성 없음")
    say(f"- 키·설정: {'등록됨' if cred in ('ok', '모델 미설정', '사용 꺼짐') else '없음'} · 모델 이름: {'설정됨' if cred not in ('키 없음', '모델 미설정') else '없음'} · 사용 켜짐: {'예' if cred == 'ok' else '아니오'} (키 값은 출력하지 않습니다)")
    say(f"- 하루 상한(daily_llm_cap) {daily_cap} · 오늘(UTC {day}) 사용 {used_today} · 남은 호출 {max(daily_cap - used_today, 0)}")
    if args.max_calls is not None:
        say(f"- 이번 실행 상한 --max-calls {args.max_calls}" + (f" < 최대 {worst}회: 상한에 닿으면 그 시점에 멈추고 '중단'으로 기록합니다." if args.max_calls < worst else ""))
    say(f"- 결과 위치: {d}/ (set.json, results.csv, summary.md, responses.jsonl, manifest.json)")

    if not live:
        say("\n호출하지 않았습니다. 실제로 하려면: P2M_LIVE=1 로 `--yes-spend --max-calls N` 을 함께 주세요(docs/live-eval-checklist.md 의 확인을 먼저).")
        return 0
    if cred != "ok":
        say(f"\n{NOT_RUN}({cred}): 호출하지 않았습니다. 관리자 패널(/admin)에서 이 공급자의 키·모델 이름을 등록하고 '사용'을 켠 뒤 다시 실행하세요.")
        return 0
    if os.environ.get("P2M_LIVE") != "1":
        say(f"\n{NOT_RUN}(P2M_LIVE=1 아님): 호출하지 않았습니다. 실제 호출은 환경변수 P2M_LIVE=1 을 명시해야 합니다.")
        return 0
    if used_today >= daily_cap:
        say(f"\n{NOT_RUN}(하루 상한 소진): 오늘 {used_today}/{daily_cap}. 호출하지 않았습니다.")
        return 0

    # --- 실제 호출 ---
    assert store is not None
    key = store.get_key(args.provider) or ""
    model = store.settings()["providers"][args.provider]["model"]
    inner = build_provider(args.provider, key_source=lambda: store.get_key(args.provider),
                           model_source=lambda: store.settings()["providers"][args.provider]["model"], adapter=adapter)
    budget = BudgetedProvider(inner, store, lambda: datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%d"), args.max_calls, daily_cap)
    d.mkdir(parents=True, exist_ok=True)
    (d / "REVIEWED").unlink(missing_ok=True)  # 다시 돌리면 이전 확인은 무효
    write_set(d, sub_set(eval_set, items, args.stage))
    results: list[ItemResult] = []
    records: list[dict] = []
    status = "완료"
    for item in items:
        try:
            r, rec = score_item(item, args.provider, budget, max_retries, key, args.tempo_tol)
        except BudgetExhausted:
            status = f"중단({budget.stopped_by} 에 닿음)"
            say(f"\n중단: {budget.stopped_by} 에 닿았습니다. {item.id} 부터는 호출하지 않았습니다.")
            break
        results.append(r)
        records.append(rec)
    if results:
        write_csv(results, d / "results.csv")
        (d / "summary.md").write_text(summary_md(results, set_name=eval_set.name, set_sha=set_sha, provider=args.provider, fallback=False, tempo_tol=args.tempo_tol), encoding="utf-8")
    with open(d / "responses.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    manifest = {
        "tool": "tools/live_eval.py", "evalkit_version": EVALKIT_VERSION, "provider": args.provider, "model": model,
        "stage": args.stage, "set_name": eval_set.name, "set_sha256": set_sha, "prompt_ids": [i.id for i in items],
        "completed_prompts": len(results), "llm_calls": budget.calls, "max_calls": args.max_calls, "max_retries": max_retries,
        "daily_llm_cap": daily_cap, "usage_day_utc": day, "fallback": False, "status": status,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 결과 폴더에 키가 섞이지 않았는지 마지막으로 확인(있으면 파일을 지우고 실패로 끝낸다)
    if key and any(key in (d / name).read_text(encoding="utf-8", errors="replace") for name in os.listdir(d) if (d / name).is_file()):
        for name in os.listdir(d):
            (d / name).unlink()
        say("오류: 결과 파일에 키 문자열이 섞여 모두 지웠습니다. 키를 교체하고 알려 주세요.")
        return 1
    ok = sum(r.generated for r in results)
    say(f"\n{status}: {len(results)}/{n} 프롬프트 처리 · 생성 성공 {ok} · LLM 호출 {budget.calls}회 · 오늘 사용량 {store.usage_today(day)}/{daily_cap}")
    say(f"쓴 폴더: {d}")
    if status == "완료":
        nxt = {"5": "20", "20": "all", "all": None}[args.stage]
        say(f"다음: 결과를 확인한 뒤 `touch {d / 'REVIEWED'}` 를 하면 다음 단계({nxt})를 열 수 있습니다." if nxt else "마지막 단계까지 끝났습니다. 결과 확인은 docs/live-eval-checklist.md 6절.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
