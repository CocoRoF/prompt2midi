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

## 실행
### 시험
```bash
pip install -e ".[dev]"
pytest
```

### 로컬 (uvicorn)
```bash
pip install -e .
P2M_DATA_DIR=./instance uvicorn prompt2midi.api.app:app --host 127.0.0.1 --port 8000
```
`http://127.0.0.1:8000/` 이 공개 화면, `/admin` 이 관리자 화면이다. `P2M_DATA_DIR`(기본 `./instance`, git 에서 제외됨)에 `admin.json`(관리자 비밀번호 해시)·`settings.json`·`secrets.json`(API 키, 권한 0600)이 저장된다.

### Docker
```bash
docker build -t prompt2midi .
docker run -d --name prompt2midi -p 8000:8000 -v p2m-data:/data prompt2midi
docker logs prompt2midi        # 최초 설정 토큰 확인
```
- 컨테이너는 비루트 사용자(uid 10001)로 돈다. 데이터 디렉터리는 `/data`(`P2M_DATA_DIR=/data`)이며 **볼륨으로 마운트해야** 재시작·재생성 뒤에도 관리자 설정과 키가 남는다. 호스트 디렉터리를 마운트할 때는 uid 10001 이 쓸 수 있어야 한다.
- 이미지에는 키를 넣지 않는다. 키는 아래 관리자 패널에서 등록한다.
- `P2M_SECRET_KEY` 를 주면 `secrets.json` 이 암호화되어 저장된다(`-e P2M_SECRET_KEY=...`, 값은 직접 만든 긴 무작위 문자열). 없으면 평문(0600) 파일이고 관리자 화면에 경고가 뜬다.

### 관리자 최초 설정
1. 서버를 처음 띄우면 관리자가 없는 상태라 서버가 무작위 **설정 토큰**을 만들어 로그(콘솔)에 한 번 출력한다. (Docker 는 `docker logs prompt2midi`)
2. `/admin` 에서 설정 토큰과 비밀번호(12자 이상)를 입력해 설정을 마친다. 토큰은 1회용이고, 이미 설정되었으면 다시 설정할 수 없다.
3. 이후 `/admin` 에서 비밀번호로 로그인한다(세션 12시간).

### 관리자 패널에서 LLM 키 등록
로그인 뒤 공급자(Anthropic·OpenAI)별로 API 키, 모델 이름, 켜기/끄기를 입력한다. 저장한 키는 다시 보이지 않고 끝 4자(`…abcd`)만 표시된다. 모델 이름이 비어 있으면 그 공급자는 쓸 수 없다. "연결 테스트" 버튼은 누를 때만 최소 호출 1회를 하며 **비용이 발생한다**. 공개 사용자의 LLM 사용은 기본으로 꺼져 있고(`public_llm`), 비용은 키 소유자가 내므로 속도 제한·일일 상한과 함께 확인한 뒤 켠다.

### 관리자 비밀번호를 잊었을 때
재설정 링크는 없다. 서버를 멈추고 데이터 디렉터리의 `admin.json` 을 삭제한 뒤 다시 띄우면 최초 설정 상태가 되어 새 설정 토큰이 로그에 출력된다. `settings.json`·`secrets.json` 은 지워지지 않으므로 등록한 키는 그대로 남는다. (Docker: `docker stop prompt2midi && docker run --rm -v p2m-data:/data busybox rm /data/admin.json && docker start prompt2midi`)

### 운영 권고
- **HTTPS 뒤에서 운영한다.** 관리자 비밀번호와 API 키 입력이 평문 HTTP 로 오가면 안 된다. nginx·Caddy 같은 리버스 프록시나 클라우드 로드밸런서에서 TLS 를 종단하고, 컨테이너 포트(8000)는 외부에 직접 열지 않는다. 세션 쿠키의 `Secure` 속성은 HTTPS 일 때만 붙는다.
- 리버스 프록시 뒤에서는 IP 별 속도 제한이 프록시 주소를 보지 않도록 클라이언트 IP 전달 설정을 맞춰야 한다(구현 방식은 백엔드 PR 참고).
- `/data` 볼륨을 백업한다. 키가 들어 있으므로 접근 권한을 제한한다.

LLM 키는 **레포·환경변수 기본값에 넣지 않는다.** 자세한 계약: [`docs/api-and-admin.md`](docs/api-and-admin.md)

## 오프라인으로 써 보기 (키·네트워크 없이)
```python
from prompt2midi.pipeline import generate
r = generate("A calm piano piece in C major, 70 BPM")   # RuleProvider(키워드 사전)가 사양을 만든다
open("calm.mid", "wb").write(r.midi)
print(r.provider_used, r.seed, [w.message for w in r.warnings])
```
- 공급자는 `FakeProvider`(시험용)와 `RuleProvider`(규칙)만 있다. LLM 공급자(Anthropic/OpenAI)는 대표님 확인 뒤 단계 4.
- 평가: `python -m prompt2midi.evaluation data/eval_prompts_research1.json --out data/eval_rule_results.csv --md docs/rule-provider-eval.md`
