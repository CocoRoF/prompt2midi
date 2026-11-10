# prompt2midi 설계안 (초안 v0.1)

- 작성: 권나경(개발 1팀) · 2026-11-10 · 상태: **대표님 확인 대기** (구현은 확인 뒤 시작)
- 근거: haru 음악 생성 조사 — 해부 보고서(01), 레퍼런스 비교표(02), 고도화 로드맵(03)의 3단계(문장 입력·규칙 번역)·4단계(LLM 번역층·규칙 폴백). 위치: 드라이브 `docs/음악생성분석/`.
- 표기: [근거] 조사 문서에 있는 것 / [설계 판단] 이 문서의 선택(근거 없는 추정 포함) / 미확인.

## 1. 목표와 비목표
**목표**: 자연어 입력 → (LLM 해석) → 구조화된 음악 사양 → 유효하고 재현 가능한 MIDI. 웹 서비스로 확장 가능한 구조.
**비목표(v0)**: 오디오 렌더링, 음원 생성 모델 연동, 사용자 계정·결제, 모델 학습/미세조정. (자료 수집은 평가·예시용이며 학습은 범위 밖)

## 2. 핵심 설계: LLM은 '사양'만 쓰고 MIDI는 코드가 만든다 [설계 판단]
```
prompt ─▶ Provider.generate_spec ─▶ MusicSpec(JSON) ─▶ SpecValidator ─▶ Compiler(seed) ─▶ MIDI bytes ─▶ MidiCheck ─▶ 응답
              ▲                          │ 위반 시: 오류 목록을 LLM에 되돌려 재시도(최대 N회)
              └────────────── 실패·키 없음: RuleProvider(규칙 기반 폴백) ───┘
```
이유: LLM이 MIDI 이벤트(틱·델타타임·노트 오프)를 직접 쓰게 하면 형식 오류와 박자 어긋남이 생기기 쉽다고 본다(추정). LLM은 **의도**(조성, 템포, 코드 진행, 모티프, 악기 역할)만 JSON으로 내고, 시간 계산과 바이트 생성은 결정적 컴파일러가 맡는다. 이렇게 하면 "정확하게 동작하는 MIDI"의 책임이 검증·컴파일러 코드에 있어 시험할 수 있다. 이는 03 로드맵이 권한 "LLM 번역층 + 규칙 엔진 유지(결정성·오프라인)" 구조와 같다 [근거: 03 §3 4단계].

## 3. 음악 사양 MusicSpec (JSON Schema는 pydantic으로 정의·내보내기)
```jsonc
{
  "version": "1",
  "title": "string(<=60)",
  "seed": 12345,                       // 컴파일러 난수. 없으면 입력 해시에서 유도
  "tempo_bpm": 96,                     // 40..220
  "time_signature": [4, 4],            // 허용 집합: 2/4 3/4 4/4 6/8 5/4 7/8 ...(확정은 구현 시)
  "key": { "tonic": "C", "mode": "minor" },   // 이명동음 표기 고정 규칙은 컴파일러에서
  "sections": [
    { "name": "A", "bars": 8,
      "chords": ["Cm", "Ab", "Eb", "Bb"],       // 마디당 1~2개, 반복 규칙은 컴파일러
      "energy": 0.4 }                            // 0..1
  ],
  "tracks": [
    { "role": "melody", "instrument": "piano",  // 역할(melody|bass|chords|pad|drums) + GM 악기 이름
      "motif": { "degrees": [1,3,5,3], "rhythm": ["q","e","e","q"] },
      "register": [60, 84] },
    { "role": "drums", "style": "rock_basic" }
  ]
}
```
- 허용 값은 열거형으로 닫는다(역할, 악기 이름→GM 번호 표, 리듬 기호, 코드 품질). 알 수 없는 값은 거부(조용히 무시하지 않음).
- 모티프는 **음계 도수 + 리듬 기호**로 쓴다(MIDI 번호가 아님). 조성에 속하는 음이 보장되고 LLM 오류 여지가 줄어든다 [설계 판단].
- 이 구조는 haru 코드가 선율을 "음계 도수 이동 + 리듬 셀"로 만드는 방식과 맞닿아 있다 [근거: 01 §2, composer.js:196-218]. 단 haru처럼 글자 코드로 음정을 만들지는 않는다.

## 4. 검증 (정확성의 핵심)
1. **스키마 검증**: 타입, 범위, 열거형, 길이 제한, 알 수 없는 필드 거부.
2. **이론 검증** (SpecValidator): 코드 이름이 파싱되고 조성과 어긋나는 정도가 허용 범위인지(비다이아토닉 코드는 경고 또는 허용 플래그), 마디 수 × 박자 = 총 길이 상한(예: 5분), 트랙·노트 수 상한, 레지스터가 악기 음역 안, 드럼 채널 규칙.
3. **MIDI 검증** (MidiCheck, 컴파일 직후): 만든 바이트를 다시 파싱해 헤더·트랙·EndOfTrack 존재, 모든 NoteOn에 NoteOff 짝, 노트 0..127, 벨로시티 1..127, 템포·박자 메타 이벤트가 사양과 일치, 총 길이가 사양과 일치.
4. **재시도**: 1~2 단계 위반 시 `{errors:[{path,message}]}`를 LLM에 되돌려 최대 N회(기본 2) 재요청. 그래도 실패하면 RuleProvider 폴백 또는 오류 응답(요청 옵션 `fallback`으로 선택).

## 5. LLM 공급자 추상화
```python
class LLMProvider(Protocol):
    name: str
    def generate_spec(self, prompt: str, schema: dict, *, feedback: list[Error] | None, seed: int | None) -> dict: ...
```
| 구현 | 역할 | 비고 |
|---|---|---|
| `AnthropicProvider` | Anthropic API | 구조화 출력(JSON Schema 강제) 사용. 키 `ANTHROPIC_API_KEY`, 모델은 `P2M_ANTHROPIC_MODEL` |
| `OpenAIProvider` | OpenAI API | 구조화 출력(JSON Schema) 사용. 키 `OPENAI_API_KEY`, 모델은 `P2M_OPENAI_MODEL` |
| `FakeProvider` | 시험용 | 고정 응답/녹화본 반환. 외부 호출 없음 |
| `RuleProvider` | 폴백 | 키워드·장르 사전으로 규칙 번역(03 3단계). 키·네트워크 없이 동작 |
- 모델 이름을 코드에 박지 않는다(환경변수). 선택은 요청의 `provider` 필드 → 없으면 `P2M_DEFAULT_PROVIDER` → 키가 있는 쪽 → `RuleProvider`.
- 키는 환경변수에서만 읽고 로그·응답·예외 메시지에 싣지 않는다. 키 없으면 해당 공급자를 "사용 불가"로 목록에서 표시.
- **미확인**: 두 공급자의 현재 구조화 출력 API의 정확한 파라미터·모델 이름·가격. 구현 직전에 각 공식 문서 원문으로 확인한다(이번 설계 단계에서는 외부 호출·문서 확인을 하지 않았다). 외부 LLM 호출은 대표님이 키 사용을 허락하기 전까지 하지 않는다.

## 6. 웹 구성 (확장 가능한 최소 구조)
- 백엔드: FastAPI (Python). 무상태 기본.
  - `POST /api/generate` `{prompt, provider?, seed?, fallback?}` → `{id, spec, midi_url, warnings, provider_used}`
  - `GET /api/midi/{id}.mid` (v0은 메모리/임시 디렉터리, 만료 있음)
  - `GET /api/providers` (사용 가능 공급자 목록, 키 값은 노출하지 않음)
  - `GET /healthz`
- 프런트: 빌드 없는 정적 페이지 1개(`web/index.html`): 프롬프트 입력, 공급자 선택, 생성 버튼, 사양 JSON 보기, MIDI 다운로드. 브라우저 재생은 v0 범위 밖(후속).
- 확장 여지(v0에서는 만들지 않음): 작업 큐(긴 요청), 저장소 추상화(S3 등), 요청 해시 캐시(`hash(prompt,provider,model,seed)`), 속도 제한·인증, 사용량 기록. 인터페이스(`Storage`, `Cache`)만 분리해 둔다.
- 비용 통제(필수): 요청당 토큰 상한, 사용자·IP당 속도 제한, 하루 호출 상한 환경변수. 상한은 대표님이 비용 한도를 정하면 반영 [미확정].

## 7. 시험 전략
| 층 | 내용 | 외부 호출 |
|---|---|---|
| 단위 | 스키마 통과/거부 사례, 이론 검증 규칙별, 코드 이름 파서 | 없음 |
| 컴파일러 | 같은 사양+시드 → **바이트 동일**; 다른 시드 → 다름; 속성 검사(노트 범위, 음이 조성 안에 드는 비율) | 없음 |
| MIDI 유효성 | MidiCheck를 컴파일 결과 전부에 적용; 일부러 깨뜨린 MIDI를 MidiCheck가 잡는지 | 없음 |
| 공급자 계약 | 녹화한 응답(fixture)을 `FakeProvider`로 재생; 잘못된 JSON·스키마 위반 응답에 대한 재시도·폴백 동작 | 없음 |
| API | TestClient로 `/api/generate` 정상·오류·폴백 | 없음 |
| 라이브(선택) | 실제 LLM 호출 평가. 환경변수 `P2M_LIVE=1` 과 키가 있을 때만 | 있음(허락 후) |
| 평가 세트 | 프롬프트 30개 정도(`data/eval_prompts.*`), 지표: 유효 MIDI 비율, 재시도 횟수, 조성 일치율 | 라이브 시 |
- 목표 수치: "유효 MIDI 100%"(검증을 통과한 것만 반환하므로 정의상 성립), 의미 충실도는 사람 평가 병행 [설계 판단]. 의미 충실도는 자동으로 완전히 측정하기 어렵다.
- haru의 교훈: 단언 없는 시험은 의미가 없다 [근거: 01 §7]. 모든 시험은 통과/실패를 가른다. CI에서 `pytest`를 돌린다.

## 8. 레포 구조와 의존성
```
prompt2midi/
├─ README.md  LICENSE.PLACEHOLDER  pyproject.toml
├─ docs/design.md            (이 문서)
├─ src/prompt2midi/
│  ├─ spec.py                MusicSpec(pydantic) + JSON Schema 내보내기
│  ├─ providers/             base.py anthropic.py openai.py fake.py rule.py registry.py
│  ├─ validate/              schema.py theory.py midicheck.py
│  ├─ compiler/              chords.py scales.py rhythm.py instruments.py drums.py render.py
│  ├─ pipeline.py            generate(prompt, opts) 오케스트레이션(재시도·폴백)
│  └─ api/                   app.py routes.py settings.py
├─ web/index.html
├─ tests/  (위 7절)
└─ data/   sources.csv  eval_prompts.jsonl  README.md
```
- 후보 의존성: pydantic, mido(MIDI 읽기·쓰기), fastapi, uvicorn, anthropic, openai, pytest. **라이선스는 확인 전** — 알려진 바로는 대체로 허용적(MIT/Apache 계열)이라고 보지만 이번에 원문을 열어 확인하지 않았다 [미확인]. 구현 전에 각 패키지의 라이선스를 `data/sources.csv`에 기록한다.
- 언어 선택(Python): 조사한 MIDI 생성 연구 코드가 Python 중심이고(02) 검증·시험 생태계가 풍부하다는 판단 [설계 판단]. 웹 프런트는 의존성 없는 정적 페이지로 시작한다.

## 9. 라이선스 (결정됨: 2026-11-10 대표님 — 비상업 조건 라이선스 → PolyForm Noncommercial 1.0.0, `LICENSE`)
> 아래는 결정 전 검토 내용(기록용). 키 관리 방식도 결정됨: 관리자 패널에서 웹 입력, 계약은 `docs/api-and-admin.md`.
- 요구: "비상업 조건의 오픈소스". **주의**: 일반적으로 '오픈소스'의 정의(OSI)는 사용 분야 제한(상업 이용 금지)을 허용하지 않는다고 알려져 있다. 비상업 조건 라이선스는 엄밀히는 "소스 공개(source-available)"다. 이 점은 법무가 확인해야 한다 [미확인: 법적 판단은 하지 않았다].
- 선택지: (a) 비상업 조건 소스 공개 라이선스(후보 이름만 제시: PolyForm Noncommercial, CC BY-NC 계열은 소프트웨어용으로 권하지 않는 의견이 있음), (b) 허용적 오픈소스 라이선스(상업 이용 허용), (c) 보류. 결정 전까지 `LICENSE.PLACEHOLDER`만 둔다.
- 의존 모델·데이터의 상업 이용 제한은 `data/sources.csv`에 항목별로 기록한다. 02에서 확인된 바: MusicGen 가중치는 비상업(요약 기준), Stable Audio Open 조건은 원문 미확인 [근거: 02]. 이 프로젝트는 외부 모델 가중치를 번들하지 않는다(LLM은 API 호출).

## 10. 자료 수집 계획 (안소율)
- 목록 형식 `data/sources.csv`: 이름, 유형(자연어-MIDI 쌍 / 음악 이론 규칙 / MIDI 코퍼스), 주소, 라이선스, **상업 이용 가능 여부(가능/불가/미확인)**, 규모, 기준일, 열람 여부(원문/요약), 용도(평가·예시·참고).
- 우선 대상(이름만, 열람 전): 자연어 설명-MIDI 쌍 데이터셋, 공개 MIDI 코퍼스, 음악 이론 규칙 자료. 구체 목록은 수집 단계에서 원문을 열어 확정.
- 재사용: 이전 조사 02의 MIDI-LLM·Text2midi 항목은 대부분 "검색 요약/논문 페이지만 확인"이었다 [근거: 02 5절]. **'text2midi 실측 결과'는 드라이브에서 찾지 못했다**(미확인; 안건 설명에 언급되었으나 우리 문서에는 없음). 경로를 알려 주시면 반영한다.

## 11. 일정(안)과 담당
| 단계 | 내용 | 담당 | 시점 |
|---|---|---|---|
| 0 | 설계안 + 레포 뼈대 | 권나경 | 오늘 |
| 1 | `spec.py`, 스키마·이론 검증, 시험 | 조현석 | 설계 확인 후 |
| 2 | 컴파일러(코드·리듬·드럼)·MidiCheck, 결정성 시험 | 조현석 | 1 이후 |
| 3 | 공급자 추상화·Fake/Rule·재시도 파이프라인 | 조현석 | 1과 병행 가능 |
| 4 | Anthropic/OpenAI 공급자(구현 직전 문서 확인, 키 허락 필요) | 조현석 | 3 이후 |
| 5 | API + 웹 화면 1개 | 공준호 | 3 이후 |
| 6 | 자료 수집 목록 | 안소율 | 병행 |
| 7 | CI·시험 정리·평가 세트 | 구본우 | 병행 |
| 8 | 통합 검토, README 실행 방법 검증, 보고 | 권나경 | 마지막 |
완료 기준: 레포에 자연어→MIDI 다운로드가 되는 최소 동작본·웹 화면 1개·시험·README·설계 문서·자료 목록(출처·라이선스·기준일), 레포 주소와 실행 방법 보고.

## 12. 대표님께 확인받을 것
1. 설계의 핵심 선택 — "LLM은 사양(JSON)만, MIDI는 코드가 생성"으로 가도 되는지.
2. 안건 해석 — '음성 생성'을 '음악(MIDI) 생성'으로 본 것, '상업 이용 X'를 비상업 공개로 본 것.
3. 라이선스 — 위 9절 선택지 중 무엇을 쓸지(법무 확인 포함).
4. LLM 키 사용 허락과 비용 상한(개발·평가용). 허락 전에는 외부 호출을 하지 않는다.
5. 안건 번호 불일치 — 안건 설명은 선행 조사를 "안건 f49dcd7a"라 하나 우리 부서의 해당 안건은 63293056이다. 같은 건으로 보고 진행했다.
