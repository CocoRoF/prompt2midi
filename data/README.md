# data/

| 파일 | 내용 |
|---|---|
| `sources.csv` | **의존 패키지**의 라이선스 기록(출처·라이선스·상업 이용·기준일). CI(`tools/check_licenses.py`)가 pyproject 의존성이 모두 적혀 있는지 검사한다 |
| `reference_datasets.csv` | **자료 수집 목록** — 평가·예시·참고용 공개 자료(자연어-MIDI 쌍, MIDI 코퍼스, 이론 자료, 기준선 후보)의 출처·라이선스(원문 문구)·상업 이용 가능 여부·기준일·열람 수준. 요약: `docs/reference-datasets.md`. 라이선스를 읽지 못한 항목은 '미확인' |
| `eval_prompts_research1.json` | 연구 1팀 프롬프트 10개(text2midi 기준선과 같은 조건 비교용) |
| `eval_rule_results.csv` | 규칙 공급자 평가 결과(`docs/rule-provider-eval.md`) |

text2midi 기준선(연구 1팀 측정): `docs/text2midi-baseline.md`.
