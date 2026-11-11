# 청취 A/B 블라인드 묶음 (`tools/listening_pack.py`)

- 안건 c544b72c(prompt2midi 3단계) · 작성: 구본우 · 입력은 `tools/compare_samples.py` 의 v1/v2 쌍 폴더
- 목적: 사람이 **어느 쪽이 v1 이고 v2 인지 모른 채** 곡마다 A/B 를 듣고 선호를 적게 한다. 지표(`proxy_*`)가 아니라 청취로 확인하기 위한 준비물이다. 이 도구는 청취를 대신하지 않고, 평가 결과를 해석하지 않는다.
- 하지 않는 것: MIDI 를 오디오로 렌더하지 않는다(사운드폰트는 법무 확인 대기). 키·네트워크·LLM·보류 세트·외부 데이터·text2midi 출력물 없음. 연구 1팀 루브릭은 아직 없어서 점수표는 `선호(A/B/동일)`·`메모`만 둔다(루브릭이 오면 열을 추가).

## 사용
```bash
python tools/compare_samples.py --out out/compare-samples            # <id>.v1.mid / <id>.v2.mid 쌍
python tools/listening_pack.py --pairs out/compare-samples --seed 7 --out out/listening-pack
```
- `--seed` 는 필수(정수). **같은 입력·같은 시드면 폴더가 바이트 동일**하다. 시드는 평가자에게 알리지 않는다(알면 배정을 다시 계산할 수 있다).
- 두 MIDI 가 바이트 동일한 곡은 기본으로 뺀다(`answer_key/excluded.csv` 에 기록). 넣으려면 `--include-identical`.
- 짝이 없는 파일(`x.v1.mid` 만 있는 경우)은 무시하고 `answer_key/pack_info.json` 에 적는다. MIDI 가 아닌 파일이 쌍에 있으면 오류(종료 코드 2).
- 다시 실행하면 `for_raters/`·`answer_key/` 를 지우고 새로 쓴다.

## 출력
```
out/listening-pack/
├─ for_raters/         ← 평가자에게 주는 것(이 폴더만 압축해 보낸다)
│   ├─ README.md       평가 안내
│   ├─ score_sheet.csv 곡, 선호(A/B/동일), 메모
│   └─ p01/ A.mid B.mid, p02/ …
└─ answer_key/         ← 평가자에게 주지 않는다(별도 위치에 보관)
    ├─ key.csv         piece, original_id, a_engine, b_engine, a_sha8, b_sha8
    ├─ excluded.csv, pack_info.json, README.md(블라인드 유지 방법)
```

## 블라인드가 지켜지는 방식
- 곡 순서(`p01`…)와 A/B 는 `sha256(시드:라벨:곡id)` 순서로 섞는다(파이썬·플랫폼 버전과 무관하게 같은 결과, 시험에 고정값). A 가 v1 인 곡과 v2 인 곡이 **정확히 반반**(곡 수가 홀수면 한 곡 차이).
- 평가자 폴더에는 `v1`·`v2`·engine·원래 곡 id·시드가 파일 이름·폴더 이름·문서·점수표·MIDI 안의 이름/텍스트 어디에도 나오지 않는다(시험이 확인).
- **한계(미확인)**: 파일 크기·연주 시간 차이로 추측할 수 있는 곡이 있을 수 있다(평가자 안내에 적어 둠). 재생 프로그램·음색에 따라 인상이 달라진다. 사람이 일부러 파일 내부를 분석하면 구별할 수도 있다 — 도구는 그것을 막지 않는다.

## 평가가 끝난 뒤
`score_sheet.csv` 의 `곡` 열을 `key.csv` 의 `piece` 열과 맞춰, `A` 선호는 `a_engine`, `B` 선호는 `b_engine` 이 선호된 쪽으로 센다(`동일` 은 어느 쪽도 아님). 표본(곡 수·평가자 수)이 작으면 우연일 수 있다. 이 결과를 임시 범위 지표(`proxy_*`)와 상관시켜 보는 것은 연구 1팀의 검증 계획이며 이 도구의 일이 아니다.
