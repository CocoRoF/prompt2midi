"""CSV per prompt + summary Markdown. No timestamps, no timings: same code + same set -> identical files."""
from __future__ import annotations

import csv
import json
from statistics import median
from pathlib import Path

from . import proxy as P
from .metrics import AXIS1_METRICS, AXIS23_METRICS, DISPLAY_TITLES, EXTRACTION_METRICS, FAIL, LISTEN_COLUMNS, METRICS, NA, PASS, TEMPO_TOLERANCE_BPM, VALUE_METRICS, VALUE_METRICS_V03
from .runner import ItemResult
from .version import EVALKIT_VERSION

LISTEN_TEXT = "청취 필요"
HEAD = ["id", "lang", "tier", "generated", "error", "provider_used", "attempts", "retries", "fallback_used", "bars", "midi_sha8"]


# Columns added after v0.1 are appended AFTER the original ones: existing names and their order never change.
EXTRA_HEAD = ["provider", "labels"]
ALIASES = {"곡ID": "id", "언어": "lang"}  # 연구 1팀 proposal names, appended last


def columns() -> list[str]:
    cols = list(HEAD)
    for m in METRICS:
        cols += [m.name, m.name + "_reason"]
    cols += list(LISTEN_COLUMNS) + EXTRA_HEAD
    cols += [name + "_value" for name in VALUE_METRICS] + ["axis1_ok"]
    return cols + list(ALIASES) + [name + "_value" for name in VALUE_METRICS_V03] + P.column_names()


def axis1_ok(r: ItemResult) -> bool:
    """Generated, and every axis 1 metric is pass or na (na = not applicable, e.g. bars_min for a prompt that states its bars)."""
    return r.generated and all(r.results[n].status in (PASS, NA) for n in AXIS1_METRICS)


def rows(results: list[ItemResult]) -> list[dict]:
    out = []
    for r in results:
        row = {"id": r.id, "lang": r.lang, "tier": r.tier, "generated": r.generated, "error": r.error, "provider_used": r.provider_used,
               "attempts": r.attempts, "retries": max(r.attempts - 1, 0), "fallback_used": r.fallback_used,
               "bars": "" if r.bars is None else r.bars, "midi_sha8": r.midi_sha8}
        for m in METRICS:
            res = r.results[m.name]
            row[m.name], row[m.name + "_reason"] = res.status, res.reason
        for c in LISTEN_COLUMNS:
            row[c] = LISTEN_TEXT
        row["provider"], row["labels"] = r.provider, ";".join(r.labels)
        for name in (*VALUE_METRICS, *VALUE_METRICS_V03):
            v = r.results[name].value
            row[name + "_value"] = "" if v is None else v
        for key, _title, _fn, names in P.PROXIES:  # v0.6: M1..M7 proxies, last columns. in_range/out_of_range/na, never pass/fail
            res = r.proxy.get(key)
            row[P.COLUMN_PREFIX + key] = res.status if res else P.NA
            row[f"{P.COLUMN_PREFIX}{key}_reason"] = res.reason if res else ""
            for n in names:
                v = res.values.get(n) if res else None
                row[f"{P.COLUMN_PREFIX}{key}_{n}"] = "" if v is None else v
        for key, n in P.EXTRA_VALUES:  # v0.7: appended after all v0.6 proxy columns
            res = r.proxy.get(key)
            v = res.values.get(n) if res else None
            row[f"{P.COLUMN_PREFIX}{key}_{n}"] = "" if v is None else v
        row["axis1_ok"] = axis1_ok(r)
        for alias, src in ALIASES.items():
            row[alias] = row[src]
        out.append(row)
    return out


DETAILS_FILE = "proxy_details.jsonl"
DETAIL_METRICS = ("m5", "m6")


def details_jsonl(results: list[ItemResult]) -> str:
    """One JSON line per piece with the per-pair (M5) and per-slot (M6) records. Same input -> same bytes (sorted keys, no times)."""
    lines = []
    for r in results:
        rec = {"id": r.id, "lang": r.lang, "tier": r.tier, "provider": r.provider}
        for k in DETAIL_METRICS:
            res = r.proxy.get(k)
            rec[k] = {"status": res.status if res else P.NA, "reason": res.reason if res else "", "details": res.details if res else {}}
        lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def write_details(results: list[ItemResult], path: Path) -> None:
    path.write_text(details_jsonl(results), encoding="utf-8")


def write_csv(results: list[ItemResult], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows(results))


def _cell(group: list[ItemResult], name: str) -> str:
    st = [r.results[name].status for r in group]
    p, f, n = st.count(PASS), st.count(FAIL), st.count(NA)
    d = p + f
    if d == 0:
        return f"— (na {n})" if n else "—"
    return f"{p}/{d} ({100 * p / d:.0f}%)" + (f" · na {n}" if n else "")


def summary_md(results: list[ItemResult], *, set_name: str, set_sha: str, provider: str, fallback: bool, tempo_tol: float | None = None) -> str:
    tiers = sorted({r.tier for r in results})
    langs = [l for l in ("ko", "en") if any(r.lang == l for r in results)]
    groups: list[tuple[str, list[ItemResult]]] = []
    for t in tiers:
        for l in langs:
            g = [r for r in results if r.tier == t and r.lang == l]
            if g:
                groups.append((f"{t}·{l}", g))
    for t in tiers:
        groups.append((f"층 {t} 전체", [r for r in results if r.tier == t]))
    for l in langs:
        groups.append((f"{l} 전체", [r for r in results if r.lang == l]))
    groups.append(("전체", list(results)))
    n = len(results)
    gen = sum(r.generated for r in results)
    tempo_text = f"±{TEMPO_TOLERANCE_BPM} BPM (기본)" if tempo_tol is None else f"±{tempo_tol:.1%} (상대, `--tempo-tol {tempo_tol}`)"
    ok1 = [r for r in results if axis1_ok(r)]
    with_key = sum(1 for r in results if "tonic" in r.labels and "mode" in r.labels)
    lines = [
        f"# evalkit 요약 — {set_name}", "",
        f"- `evalkit_version` {EVALKIT_VERSION} · 세트: `{set_name}` ({n}개, 파일 sha256 앞 8자 `{set_sha[:8]}`) · 공급자: `{provider}` · 폴백: {'켬' if fallback else '끔(공급자만 측정)'}",
        f"- 허용오차: 템포 {tempo_text} · 마디 수 정확 일치 · 음계 이탈 ≤10% (genre 라벨에 jazz·blues 가 있으면 ≤25%) · 악기 음역 밖 ≤5% · 길이 ≥8마디",
        f"- 유효 곡 비율: 생성 성공 {gen}/{n} · 축 1 통과(`axis1_ok`) {len(ok1)}/{n}. 시각·시간 값은 넣지 않는다(같은 코드·같은 세트면 같은 파일).",
        "", "## 곡(MIDI) 지표별 통과 비율", "",
        "셀 = `통과/분모 (비율)`. **분모는 pass+fail 만**이고, 라벨이 없거나 해당 없는 항목(`na`)은 분모에서 뺀 뒤 `· na N` 으로 따로 적는다. 분모가 0이면 `—`.",
        f"음계 이탈(2-3)은 tonic·mode 라벨이 **둘 다 있는 항목만** 채점한다: 이 세트에서 {with_key}/{n}개.", "",
        "| 지표 | " + " | ".join(g for g, _ in groups) + " |", "|---|" + "---|" * len(groups),
        f"| 프롬프트 수 | " + " | ".join(str(len(g)) for _, g in groups) + " |",
    ]
    titles = {m.name: DISPLAY_TITLES.get(m.name, m.title) for m in METRICS}
    for m in METRICS:
        if m.name not in EXTRACTION_METRICS:
            lines.append(f"| {titles[m.name]} (`{m.name}`) | " + " | ".join(_cell(g, m.name) for _, g in groups) + " |")
    lines += ["", "## 사양 추출 일치 (라벨 ↔ 사양 선언)", "",
              "곡(MIDI)이 아니라 **문장에서 뽑아 사양에 적은 값**이 라벨과 맞는지 본다. 연구 1팀 축 2·3 이 아니며 축 1 통과 여부로 거르지 않는다(모든 곡 대상, 분모 규칙은 위와 같다). `조성 선언 일치`는 연구 1팀 2-2(사양 조 ↔ 곡에서 추정한 조)와 **다른 지표**다.",
              "<!-- Intent v1 일치율 지표(연구 1팀 3-1)가 들어올 자리: metrics.EXTRACTION_METRICS 에 추가하면 이 표에 나온다 -->", "",
              "| 지표 | " + " | ".join(g for g, _ in groups) + " |", "|---|" + "---|" * len(groups)]
    for name in EXTRACTION_METRICS:
        lines.append(f"| {titles[name]} (`{name}`) | " + " | ".join(_cell(g, name) for _, g in groups) + " |")
    ok_groups = [(g, [r for r in rs if axis1_ok(r)]) for g, rs in groups]
    lines += ["", "## 축 1 통과 곡만의 집계 (축 2·3 지표)", "",
              "연구 1팀 규칙: 축 1(MIDI 유효성) 실패 곡은 이후 축 2·3 집계에서 뺀다. 축 1 = " + ", ".join(f"`{m}`" for m in AXIS1_METRICS) + " (모두 pass 또는 na). 1-8 중 빈 트랙(`no_empty_tracks`)은 파일 구조 결함이라 축 1 에 두고, 악기 구성(`midi_instruments_included`)만 축 1 에서 빼 축 3(3-7)으로 집계한다. 두 값은 `no_empty_tracks_value`·`midi_instruments_included_value` 열에 기록한다. 사양 추출 지표는 이 표에 넣지 않는다(위 절). 분모 규칙은 위와 같다.", "",
              "| 지표 | " + " | ".join(g for g, _ in ok_groups) + " |", "|---|" + "---|" * len(ok_groups),
              "| 축 1 통과 곡 수 / 전체 | " + " | ".join(f"{len(rs)}/{len(all_g)}" for (_, rs), (_, all_g) in zip(ok_groups, groups)) + " |"]
    for name in AXIS23_METRICS:
        lines.append(f"| {titles[name]} (`{name}`) | " + " | ".join(_cell(rs, name) for _, rs in ok_groups) + " |")
    lines += ["", "## 재시도·폴백 (참고, 합격 판정 아님)", "",
              "| 그룹 | 재시도가 있었던 프롬프트 | 폴백으로 만든 프롬프트 | 생성 실패 |", "|---|---|---|---|"]
    for gname, g in groups:
        lines.append(f"| {gname} | {sum(r.attempts > 1 for r in g)}/{len(g)} | {sum(r.fallback_used for r in g)}/{len(g)} | {sum(not r.generated for r in g)}/{len(g)} |")
    lines += ["", "## 대리 지표 M1~M7 — 합격 판정이 아님", "",
              "연구 1팀 정의서(`자동_대리지표_정의.md`)의 대리 지표. 판정 이름은 `in_range`/`out_of_range`/`na` 이며 **pass/fail 집계, `axis1_ok` 와 섞지 않는다.** "
              "**범위(임계값)는 전부 임시값이고 청취 상관 검증 전에는 음악 품질의 근거나 서명 지표로 쓰지 않는다.** 규칙 변경 전/후 비교와 회귀 탐지용. "
              "`midicheck_valid`·`note_integrity` 실패 곡은 `na`. **M5 의 d_* 열은 인접 섹션 쌍 중 최댓값, M6 은 평균이며, 쌍별·슬롯별 값은 `proxy_details.jsonl` 에 있다.** 정의·한계: `src/prompt2midi/evalkit/proxy.py`, 해석: `docs/baseline-expressiveness-v0.md`. CSV 열은 `proxy_` 로 시작한다.", "",
              "| 지표 | in_range | out_of_range | na | 범위 안 비율(분모=in+out) |", "|---|---|---|---|---|"]
    for key, title, _fn, _names in P.PROXIES:
        st = [r.proxy[key].status if key in r.proxy else P.NA for r in results]
        i_, o_, n_ = st.count(P.IN_RANGE), st.count(P.OUT_OF_RANGE), st.count(P.NA)
        lines.append(f"| {title} (`{P.COLUMN_PREFIX}{key}`) | {i_} | {o_} | {n_} | " + (f"{i_}/{i_ + o_} ({100 * i_ / (i_ + o_):.0f}%)" if i_ + o_ else "—") + " |")
    lines += ["", "값의 분포(측정된 곡만, 합격 기준 아님):", "", "| 값 | 측정된 곡 수 | 최솟값 | 중앙값 | 최댓값 |", "|---|---|---|---|---|"]
    for key, _title, _fn, names in P.PROXIES:
        for nm in (*names, *(n for k2, n in P.EXTRA_VALUES if k2 == key and n != "voicing_track")):
            vals = sorted(float(r.proxy[key].values[nm]) for r in results if key in r.proxy and r.proxy[key].values.get(nm) is not None)
            stats = f"{vals[0]:.4f} | {median(vals):.4f} | {vals[-1]:.4f}" if vals else "— | — | —"
            lines.append(f"| `{P.COLUMN_PREFIX}{key}_{nm}` | {len(vals)}/{n} | {stats} |")
    lines += ["", "## 청취가 필요한 항목", "",
              f"무드 부합·음악성은 **점수화하지 않는다.** CSV 의 `{LISTEN_COLUMNS[0]}`, `{LISTEN_COLUMNS[1]}` 열은 전 {n}개 모두 `{LISTEN_TEXT}`이며 청취 루브릭(연구 1팀)으로 채점한다.", ""]
    fails = [(r.id, m.name, r.results[m.name].reason) for r in results for m in METRICS if r.results[m.name].status == FAIL]
    lines += ["## 실패 목록", ""]
    lines += [f"- `{i}` · `{name}`: {why}" for i, name, why in fails] if fails else ["없음"]
    lines += ["", "## 읽는 법과 한계", "",
              "- 자동 지표는 프롬프트에 **글자로 적힌 속성**이 결과에 반영됐는지만 본다. 음악이 좋은지, 분위기가 맞는지는 보지 않는다.",
              "- 템포·조성·박자표·마디·악기는 라벨이 있는 프롬프트만 채점한다. 라벨이 적은 세트에서는 분모가 작아 비율이 크게 흔들린다.",
              "- `악기 선언 일치`는 사양의 트랙 악기 기준(사양 추출), `악기 포함(MIDI 프로그램)`은 MIDI 프로그램 번호 기준(곡)이다.",
              "- 정의·합격 판정: `docs/eval-metrics.md` (연구 1팀 서명 대기).", ""]
    return "\n".join(lines)
