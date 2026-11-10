# prompt2midi

자연어 설명을 LLM(Anthropic · OpenAI 교체 가능)이 **검증 가능한 음악 사양(JSON)** 으로 해석하고,
결정적(deterministic) 컴파일러가 그 사양을 **MIDI 파일**로 만드는 오픈소스 프로젝트. 웹 서비스로 확장할 수 있는 구조.

> 상태: 개발 중. 키 없이 도는 코어(사양·검증·컴파일러·규칙 공급자·파이프라인)가 있고, 웹 API·화면·LLM 공급자(Anthropic/OpenAI)·관리자 패널을 만드는 중이다. 계약: [`docs/api-and-admin.md`](docs/api-and-admin.md)
> 라이선스: **PolyForm Noncommercial 1.0.0** ([`LICENSE`](LICENSE)). 비상업 목적에 한해 사용·수정·배포할 수 있다. OSI 정의의 오픈소스가 아니라 소스 공개(source-available) 라이선스이며, 법무 확인은 별도 과제다.

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
LLM 키는 **레포·환경변수에 넣지 않는다.** 앱의 관리자 패널(`/admin`)에서 관리자가 웹으로 입력해 등록한다(서버의 데이터 디렉터리에만 저장, 응답·로그에 노출하지 않음). 자세한 내용: `docs/api-and-admin.md`

## 오프라인으로 써 보기 (키·네트워크 없이)
```python
from prompt2midi.pipeline import generate
r = generate("A calm piano piece in C major, 70 BPM")   # RuleProvider(키워드 사전)가 사양을 만든다
open("calm.mid", "wb").write(r.midi)
print(r.provider_used, r.seed, [w.message for w in r.warnings])
```
- 공급자: `RuleProvider`(규칙, 키 불필요), `FakeProvider`(시험용), `AnthropicProvider`·`OpenAIProvider`(키·모델은 앱의 관리자 패널로 등록해 요청마다 읽음; 확인 기록은 `docs/providers.md`). 키는 코드·설정 파일·환경변수에 두지 않는다(`docs/api-and-admin.md`).
- 평가: `python -m prompt2midi.evaluation data/eval_prompts_research1.json --out data/eval_rule_results.csv --md docs/rule-provider-eval.md`

## CI
`.github/workflows/ci.yml`: push·PR 마다 Python 3.11~3.14 에서 `pytest`(LLM 실제 호출 없음, `P2M_LIVE` 미설정), 비밀 문자열 스캔(`tools/scan_secrets.py`), 의존성 라이선스 기록 검사(`tools/check_licenses.py`: `pyproject.toml` 의 의존성이 `data/sources.csv` 에 모두 있어야 함)를 돌린다.
비밀 스캔 오탐(시험용 가짜 키 등)은 해당 줄 끝에 `# pragma: allowlist secret` 주석을 달아 제외한다.

## 서버 실행 (웹 API + 화면)
```bash
pip install -e ".[dev]"
uvicorn prompt2midi.api.app:app --host 127.0.0.1 --port 8000     # 데이터는 ./instance (P2M_DATA_DIR 로 바꿀 수 있음)
```
- 키 없이도 `http://127.0.0.1:8000/` 에서 규칙 공급자로 "문장 → MIDI 다운로드"가 된다. 관리자 화면은 `/admin`.
- **관리자 최초 설정**: 관리자가 아직 없으면 서버가 시작할 때 1회용 **설정 토큰**을 콘솔(서버 로그)에 한 줄 출력한다. `/admin` 에서 토큰과 비밀번호(12자 이상)를 넣어 설정한다.
- **LLM 키 등록**: `/admin` 로그인 → 공급자 카드에 API 키·모델 이름 입력 → "사용" 켜기 → "공개 설정"에서 `public_llm` 을 켜야 일반 사용자가 LLM 을 고를 수 있다(기본 꺼짐; 비용은 키 소유자가 낸다). 키는 코드·설정 파일·환경변수 어디에도 두지 않는다. `P2M_SECRET_KEY` 를 지정하면 저장된 키가 암호화된다(지정하지 않으면 0600 평문 파일이며 관리자 화면에 경고가 뜬다).
- **관리자 비밀번호를 잊었다면**: 서버를 멈추고 데이터 디렉터리의 `admin.json` 을 지운 뒤 다시 시작하면 새 설정 토큰이 출력된다(공급자 설정과 키는 그대로 남는다).
- 환경변수와 구현 세부: [`docs/api-and-admin.md`](docs/api-and-admin.md) 9절. 프록시 뒤에서 쓰면 `P2M_TRUST_PROXY=1`.
- 손으로 확인: `python tools/live_smoke.py` (실제 uvicorn 을 띄워 키 없는 흐름을 HTTP 로 점검).
