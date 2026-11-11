# prompt2midi

자연어 설명을 LLM(Anthropic · OpenAI 교체 가능)이 **검증 가능한 음악 사양(JSON)** 으로 해석하고,
결정적(deterministic) 컴파일러가 그 사양을 **MIDI 파일**로 만드는 오픈소스 프로젝트. 웹 서비스로 확장할 수 있는 구조.

> 상태: v0 — 키 없이도 규칙 공급자로 '자연어 → MIDI 다운로드'가 되는 웹 서버(FastAPI)와 화면(`/`, 관리자 `/admin`)이 동작한다. Anthropic·OpenAI 공급자는 구현·시험(가짜 전송)까지 되어 있고, **실제 API 호출은 관리자가 패널에 키를 등록한 뒤에야 검증할 수 있다(미검증)**. 실행 방법은 아래 '서버 실행', 계약은 [`docs/api-and-admin.md`](docs/api-and-admin.md).
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
| `src/prompt2midi/api/` | 웹 백엔드 (FastAPI) |
| `web/` | 웹 화면 (빌드 없는 정적 페이지 1개로 시작) |
| `tests/` | 시험 |
| `data/` | 자료 수집 목록·평가 프롬프트 (출처·라이선스·기준일) |
| `docs/` | 설계 문서 |

## 시험
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
### 로컬
```bash
pip install -e ".[dev]"
python -m prompt2midi.api --host 127.0.0.1 --port 8000 --data-dir ./instance     # 옵션은 모두 선택(기본값 127.0.0.1, 8000, $P2M_DATA_DIR 또는 ./instance)
```
- **이 진입점을 쓴다.** 내부에서 uvicorn 을 `proxy_headers=False` 로 띄운다. uvicorn 기본값(`--proxy-headers`)은 127.0.0.1 에서 온 `X-Forwarded-For` 를 믿고 클라이언트 주소를 바꾸므로, 그대로 두면 헤더 하나로 관리자 로그인 실패 제한(IP 당 5회)을 우회할 수 있다(구본우 확인: 헤더를 바꿔 가며 7번 시도해도 모두 401). 프록시 뒤에서의 설정은 아래 '운영 권고'.
- `uvicorn prompt2midi.api.app:app` 을 직접 실행해도 되지만 그때는 **`--no-proxy-headers` 가 필수**다: `P2M_DATA_DIR=./instance uvicorn prompt2midi.api.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers`. 빼먹으면 앱이 (가능한 범위에서) 경고 로그를 남기지만 막지는 못한다.
- 키 없이도 `http://127.0.0.1:8000/` 에서 규칙 공급자로 "문장 → MIDI 다운로드"가 된다. 관리자 화면은 `/admin`.
- 데이터 디렉터리 `P2M_DATA_DIR`(기본 `./instance`, git 에서 제외됨)에 `admin.json`·`settings.json`·`secrets.json`·`usage.json` 이 0600 으로 저장된다.
- 화면 폴더는 `P2M_WEB_DIR` 이 없으면 저장소의 `web/`, 없으면 현재 디렉터리의 `web/` 를 찾는다.
- 손으로 확인: `python tools/live_smoke.py` (실제 uvicorn 을 띄워 키 없는 흐름을 HTTP 로 점검).

### Docker
```bash
docker build -t prompt2midi .
docker run -d --name prompt2midi -p 8000:8000 -v p2m-data:/data prompt2midi
docker logs prompt2midi        # 최초 설정 토큰 확인
```
- 컨테이너는 비루트 사용자(uid 10001)로 돌고, 데이터 디렉터리는 `/data`(`P2M_DATA_DIR=/data`)다. **`/data` 를 볼륨으로 마운트해야** 재시작·재생성 뒤에도 관리자 설정과 키가 남는다. 호스트 디렉터리를 마운트하면 uid 10001 이 쓸 수 있어야 한다.
- 이미지에는 키를 넣지 않는다. 화면은 `/app/web`(`P2M_WEB_DIR`)에서 서빙한다.
- `-e P2M_SECRET_KEY=<직접 만든 긴 무작위 문자열>` 을 주면 저장된 키가 암호화된다(지정하지 않으면 0600 평문 파일이며 관리자 화면에 경고가 뜬다). 이 값을 잃으면 저장된 키는 못 읽으므로 관리자가 다시 입력해야 한다.

### 관리자 최초 설정
1. 관리자가 아직 없으면 서버가 시작할 때 1회용 **설정 토큰**을 서버 로그(콘솔)에 한 줄 출력한다(재시작해도 관리자가 이미 있으면 출력되지 않는다).
   ```
   [prompt2midi] 관리자 최초 설정 토큰(1회용, 이 줄은 한 번만 출력됩니다): <토큰>
   ```
   Docker 는 `docker logs prompt2midi` 로 찾는다.
2. `/admin` 에서 토큰과 비밀번호(12자 이상)를 넣어 설정한다. 이미 설정되었으면 409 `already_configured`.
3. 이후 `/admin` 에서 비밀번호로 로그인한다(세션 12시간, 실패는 IP 당 10분에 5회까지).

### LLM 키 등록
`/admin` 로그인 → 공급자(Anthropic·OpenAI) 카드에 API 키·모델 이름 입력 → "사용" 켜기. 저장한 키는 다시 보이지 않고 끝 4자(`…abcd`)만 표시된다. 모델 이름이 비어 있으면 그 공급자는 쓸 수 없다. "연결 테스트"는 누를 때만 최소 호출 1회를 하며 **비용이 발생한다**. 일반 사용자가 LLM 을 고르게 하려면 "공개 설정"에서 `public_llm` 을 켠다(기본 꺼짐; 비용은 키 소유자가 낸다). 키는 코드·설정 파일·환경변수 어디에도 두지 않는다.

### 관리자 비밀번호를 잊었다면
재설정 링크는 없다. 서버를 멈추고 데이터 디렉터리의 **`admin.json` 만** 지운 뒤 다시 시작하면 새 설정 토큰이 로그에 출력된다. 공급자 설정(`settings.json`)과 키(`secrets.json`)는 그대로 남는다.
Docker: `docker stop prompt2midi && docker run --rm -v p2m-data:/data busybox rm /data/admin.json && docker start prompt2midi && docker logs prompt2midi`

### 운영 권고
- **HTTPS 뒤에서 운영한다.** 관리자 비밀번호와 API 키가 평문 HTTP 로 오가면 안 된다. nginx·Caddy 같은 리버스 프록시나 로드밸런서에서 TLS 를 종단하고 컨테이너 포트(8000)는 외부에 직접 열지 않는다. 세션 쿠키의 `Secure` 는 HTTPS 일 때만 붙는다(프록시 뒤라면 `P2M_TRUST_PROXY=1` 이어야 `X-Forwarded-Proto` 를 본다).
- **프록시 뒤에서는 `P2M_TRUST_PROXY=1`**: 속도 제한·로그인 실패 제한이 클라이언트 IP 를 `X-Forwarded-For` 의 **마지막** 항목으로 센다. 프록시 뒤에서만 켠다 — 꺼 두면 헤더는 무시되어 헤더로 제한을 우회할 수 없다(계약 `docs/api-and-admin.md` 9절).
- **uvicorn 의 proxy 헤더 처리는 끈다(`python -m prompt2midi.api` 가 자동으로, `uvicorn` 직접 실행이면 `--no-proxy-headers`).** uvicorn 은 기본으로 127.0.0.1 에서 온 `X-Forwarded-*` 를 믿고 클라이언트 주소를 바꾼다. 그러면 `P2M_TRUST_PROXY` 를 켜지 않았어도 같은 호스트의 프록시가 전달한 헤더로 IP 가 바뀌어, 앱이 헤더 처리를 직접 하는 위 규칙과 어긋난다. Docker 이미지는 `python -m prompt2midi.api` 로 시작한다. 직접 `uvicorn` 을 쓰면 `--no-proxy-headers` 를 붙인다.
- `/data` 를 백업하고 접근 권한을 제한한다(키가 들어 있다).
- 환경변수와 구현 세부: [`docs/api-and-admin.md`](docs/api-and-admin.md) 9절.
