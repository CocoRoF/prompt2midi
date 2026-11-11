"""CSV per prompt + summary Markdown. No timestamps, no timings: same code + same set -> identical files."""
from __future__ import annotations

import csv
from pathlib import Path

from .metrics import FAIL, LISTEN_COLUMNS, METRICS, NA, PASS
from .runner import ItemResult

LISTEN_TEXT = "청취 필요"
HEAD = ["id", "lang", "tier", "generated", "error", "provider_used", "attempts", "retries", "fallback_used", "bars", "midi_sha8"]


def columns() -> list[str]:
    cols = list(HEAD)
    for m in METRICS:
        cols += [m.name, m.name + "_reason"]
    return cols + list(LISTEN_COLUMNS)


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
        out.append(row)
    return out


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


def summary_md(results: list[ItemResult], *, set_name: str, set_sha: str, provider: str, fallback: bool) -> str:
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
    lines = [
        f"# evalkit 요약 — {set_name}", "",
        f"- 세트: `{set_name}` ({n}개, 파일 sha256 앞 8자 `{set_sha[:8]}`) · 공급자: `{provider}` · 폴백: {'켬' if fallback else '끔(공급자만 측정)'}",
        f"- 생성 성공 {gen}/{n}. 시각·시간 값은 넣지 않는다(같은 코드·같은 세트면 같은 파일).",
        "", "## 지표별 통과 비율", "",
        "셀 = `통과/분모 (비율)`. **분모는 pass+fail 만**이고, 라벨이 없거나 해당 없는 항목(`na`)은 분모에서 뺀 뒤 `· na N` 으로 따로 적는다. 분모가 0이면 `—`.", "",
        "| 지표 | " + " | ".join(g for g, _ in groups) + " |", "|---|" + "---|" * len(groups),
        f"| 프롬프트 수 | " + " | ".join(str(len(g)) for _, g in groups) + " |",
    ]
    for m in METRICS:
        lines.append(f"| {m.title} (`{m.name}`) | " + " | ".join(_cell(g, m.name) for _, g in groups) + " |")
    lines += ["", "## 재시도·폴백 (참고, 합격 판정 아님)", "",
              "| 그룹 | 재시도가 있었던 프롬프트 | 폴백으로 만든 프롬프트 | 생성 실패 |", "|---|---|---|---|"]
    for gname, g in groups:
        lines.append(f"| {gname} | {sum(r.attempts > 1 for r in g)}/{len(g)} | {sum(r.fallback_used for r in g)}/{len(g)} | {sum(not r.generated for r in g)}/{len(g)} |")
    lines += ["", "## 청취가 필요한 항목", "",
              f"무드 부합·음악성은 **점수화하지 않는다.** CSV 의 `{LISTEN_COLUMNS[0]}`, `{LISTEN_COLUMNS[1]}` 열은 전 {n}개 모두 `{LISTEN_TEXT}`이며 청취 루브릭(연구 1팀)으로 채점한다.", ""]
    fails = [(r.id, m.name, r.results[m.name].reason) for r in results for m in METRICS if r.results[m.name].status == FAIL]
    lines += ["## 실패 목록", ""]
    lines += [f"- `{i}` · `{name}`: {why}" for i, name, why in fails] if fails else ["없음"]
    lines += ["", "## 읽는 법과 한계", "",
              "- 자동 지표는 프롬프트에 **글자로 적힌 속성**이 결과에 반영됐는지만 본다. 음악이 좋은지, 분위기가 맞는지는 보지 않는다.",
              "- 템포·조성·박자표·마디·악기는 라벨이 있는 프롬프트만 채점한다. 라벨이 적은 세트에서는 분모가 작아 비율이 크게 흔들린다.",
              "- 악기 포함은 사양의 트랙 악기를 기준으로 한다(MIDI 프로그램 번호 직접 검사는 하지 않음).",
              "- 정의·합격 판정: `docs/eval-metrics.md` (연구 1팀 서명 대기).", ""]
    return "\n".join(lines)
