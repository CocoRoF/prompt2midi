# prompt2midi

자연어 설명을 LLM(Anthropic · OpenAI 교체 가능)이 **검증 가능한 음악 사양(JSON)** 으로 해석하고,
결정적(deterministic) 컴파일러가 그 사양을 **MIDI 파일**로 만드는 오픈소스 프로젝트. 웹 서비스로 확장할 수 있는 구조.

> 상태: **설계 단계**. 구현은 설계안(`docs/design.md`)을 대표님이 확인한 뒤 시작한다. 지금은 레포 뼈대만 있다.
> 라이선스: **미정** (비상업 조건 예정). `LICENSE.PLACEHOLDER` 참고. 본문이 들어가기 전까지는 사용 허가가 없다.

## 구조 한눈에
```
prompt ─▶ LLM provider ─▶ MusicSpec(JSON) ─▶ validate ─▶ compile(seeded) ─▶ .mid
              ▲ 실패 시 오류 피드백(재시도)        └ 규칙 기반 폴백(키·네트워크 없이도 동작)
```
자세한 내용: [`docs/design.md`](docs/design.md)

## 폴더
| 경로 | 내용 |
|---|---|
| `src/prompt2midi/providers/` | LLM 공급자 추상화 (Anthropic, OpenAI, 시험용 Fake, 규칙 폴백) |
| `src/prompt2midi/validate/` | 사양 스키마 검증 + 음악 이론 검증 |
| `src/prompt2midi/compiler/` | 사양 → MIDI (시드 고정, 같은 입력=같은 바이트) |
| `src/prompt2midi/api/` | 웹 백엔드 (FastAPI 예정) |
| `web/` | 웹 화면 (빌드 없는 정적 페이지 1개로 시작) |
| `tests/` | 시험 |
| `data/` | 자료 수집 목록·평가 프롬프트 (출처·라이선스·기준일) |
| `docs/` | 설계 문서 |

## 실행 (현재는 뼈대 시험만)
```bash
pip install -e ".[dev]"
pytest
```
LLM 키는 환경변수(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)로만 받는다. 코드·설정 파일·로그에 키를 두지 않는다.

## 오프라인으로 써 보기 (키·네트워크 없이)
```python
from prompt2midi.pipeline import generate
r = generate("A calm piano piece in C major, 70 BPM")   # RuleProvider(키워드 사전)가 사양을 만든다
open("calm.mid", "wb").write(r.midi)
print(r.provider_used, r.seed, [w.message for w in r.warnings])
```
- 공급자는 `FakeProvider`(시험용)와 `RuleProvider`(규칙)만 있다. LLM 공급자(Anthropic/OpenAI)는 대표님 확인 뒤 단계 4.
- 평가: `python -m prompt2midi.evaluation data/eval_prompts_research1.json --out data/eval_rule_results.csv --md docs/rule-provider-eval.md`
