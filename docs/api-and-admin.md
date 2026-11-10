# API 와 관리자 패널 계약 (v0.1)

- 안건 c5b5fa12 · 작성: 권나경 · 대표님 결정 반영(2026-11-10): **LLM 키는 레포·설정 파일·환경변수 기본값에 넣지 않는다. 앱의 관리자 패널(웹)에서 관리자가 입력해 등록한다.**
- 이 문서는 백엔드(조현석)·프런트(공준호)·CI/배포(구본우)가 같은 계약으로 병렬 작업하기 위한 것이다. 바꿔야 하면 이 문서를 먼저 고친다.
- 표기: **필수**는 PR 병합 조건. 미정은 "구현 시 확정"으로 두고 PR 본문에 어떻게 정했는지 적는다.

## 1. 구성
- 백엔드: FastAPI (Python ≥3.11). 정적 화면을 같은 프로세스가 서빙(`/` 공개 화면, `/admin` 관리자 화면). 빌드 단계 없는 HTML/JS.
- 상태 저장: 데이터 디렉터리 `P2M_DATA_DIR`(기본 `./instance`, **`.gitignore` 필수**). 파일: `admin.json`(관리자 비밀번호 해시), `settings.json`(제한·공급자 설정), `secrets.json`(API 키). 키 파일 권한 0600.
- 생성된 MIDI는 메모리/임시 디렉터리에 TTL(기본 1시간)·최대 개수로 보관. ID는 `secrets.token_urlsafe` 불투명 값.

## 2. 공개 API (인증 없음)
| 메서드 경로 | 요청 | 응답 |
|---|---|---|
| `GET /healthz` | – | `{"ok": true}` |
| `GET /api/providers` | – | `[{"id":"rule","label":"Rule (offline)","available":true}, {"id":"anthropic","label":"...","available":bool}, {"id":"openai",...}]` — `available`은 "키가 등록되고 관리자가 켰고 공개 허용인 경우"만 true. 키 값·힌트는 노출하지 않는다 |
| `POST /api/generate` | `{"prompt": str(1..max_prompt_chars), "provider"?: "rule"\|"anthropic"\|"openai", "seed"?: int, "fallback"?: bool=true}` | 200 `{"id", "spec", "midi_url", "provider_used", "seed", "warnings":[{path,message}], "attempts":[{provider, ok, errors:[{path,message}]}]}` |
| `GET /api/midi/{id}.mid` | – | `audio/midi`, `Content-Disposition: attachment`, 없으면/만료면 404 |
- 오류 형식: `{"error": {"code": str, "message": str, "details"?: ...}}`. 코드: 400 `invalid_input`, 404 `not_found`, 422 `generation_failed`(details에 attempts), 429 `rate_limited` / `daily_cap_reached`, 503 `provider_unavailable`.
- 공개 사용자가 LLM 공급자를 쓸 수 있는지는 관리자 설정 `public_llm`(기본 **false**)이 정한다. false면 `provider`가 anthropic/openai여도 `rule`로 처리하지 않고 403 `llm_disabled`(조용히 바꾸지 않는다). 이유: 비용은 키 소유자가 낸다.
- 속도 제한(공개): IP당 분당 N회(기본 10), 하루 LLM 호출 상한(기본 100, 합산). 관리자 패널에서 조정.

## 3. 관리자 인증
- 첫 실행(관리자 미설정): 서버가 무작위 **설정 토큰**을 만들어 서버 로그(콘솔)에 한 번 출력한다. `GET /api/admin/status` → `{"configured": false}`.
- `POST /api/admin/setup` `{"setup_token", "password"(≥12자)}` → 설정 완료. 토큰은 1회용. 이미 설정되었으면 409.
- `POST /api/admin/login` `{"password"}` → 세션 쿠키(`HttpOnly; SameSite=Strict; Secure`는 HTTPS일 때) + 응답 본문에 `csrf_token`. 세션 만료 12시간. 로그인 실패는 IP당 제한(기본 5회/10분)과 지연.
- 이후 모든 `/api/admin/*` 변경 요청(POST/PUT/DELETE)은 헤더 `X-CSRF-Token` 필요. 틀리면 403.
- 비밀번호는 표준 라이브러리 `hashlib.scrypt`(솔트 포함)로 해시 저장. 평문·재설정 링크 없음(분실 시 `admin.json` 삭제 후 재설정 — README에 적을 것).
- `POST /api/admin/logout`.

## 4. 관리자 API (세션+CSRF 필요)
| 메서드 경로 | 요청 | 응답/동작 |
|---|---|---|
| `GET /api/admin/settings` | – | `{"providers":{"anthropic":{"key_set":bool,"key_hint":"…abcd","model":str,"enabled":bool},"openai":{...}},"limits":{"requests_per_minute":int,"daily_llm_cap":int,"max_prompt_chars":int,"max_retries":int},"public_llm":bool,"default_provider":str,"storage":{"secrets_encrypted":bool}}` — **키 전체값은 어떤 응답에도 없다**. `key_hint`는 끝 4자만 |
| `PUT /api/admin/providers/{id}` | `{"api_key"?: str, "model"?: str, "enabled"?: bool}` | `api_key`는 쓰기 전용(비어 있으면 변경 없음). 저장 후 검증 호출은 하지 않는다 |
| `DELETE /api/admin/providers/{id}/key` | – | 키 삭제 |
| `POST /api/admin/providers/{id}/test` | – | **관리자가 버튼을 눌렀을 때만** 최소 호출(작은 max tokens) 1회. 성공/실패와 지연만 반환, 오류 메시지에서 키 문자열 제거 |
| `PUT /api/admin/limits` | 위 `limits` 일부 | 값 범위 검증 |
| `PUT /api/admin/public` | `{"public_llm": bool, "default_provider"?: str}` | – |
- 키 저장: `P2M_SECRET_KEY` 환경변수(선택)가 있으면 그 값에서 유도한 키로 암호화해 `secrets.json`에 저장(라이브러리 선택은 구현 시 확정, 새 의존성은 `data/sources.csv`에 라이선스 기록). 없으면 평문 0600 파일이고 `storage.secrets_encrypted=false`로 알리며 관리자 화면에 경고를 띄운다.
- 키는 **로그·예외 메시지·응답·시험 출력 어디에도 남기지 않는다**. 공급자 SDK 오류는 키 부분 문자열을 마스킹한 뒤 반환. 시험에서 로그 캡처로 확인(필수).

## 5. LLM 공급자 (Anthropic, OpenAI)
- `LLMProvider` 프로토콜(`providers/base.py`)을 구현. 요청마다 저장소에서 키를 읽어 클라이언트를 만든다(키 변경 즉시 반영).
- 구조화 출력(JSON Schema 강제) 사용을 우선. **각 공급자의 현재 공식 문서 원문으로 파라미터·모델 이름을 확인한 뒤** `docs/providers.md`에 확인일과 URL을 적는다(기억에 의존 금지). 모델 이름은 코드에 박지 않고 관리자 패널의 `model` 설정에서 받는다(빈 값이면 공급자 사용 불가).
- 시험은 네트워크 없이: SDK 호출부를 얇은 어댑터 뒤에 두고 어댑터를 가짜로 바꿔 재시도·피드백·키 마스킹을 검증. 실제 호출 시험은 `P2M_LIVE=1` + 관리자가 등록한 키가 있을 때만(CI에서는 건너뜀).
- 사용자 입력(프롬프트)은 신뢰하지 않는다: 시스템 프롬프트와 분리하고, 출력은 반드시 스키마·이론 검증을 통과해야 MIDI가 된다(이미 구현된 파이프라인).

## 6. 화면 (공개 `/`, 관리자 `/admin`)
- 공개: 프롬프트 입력(글자 수 표시), 공급자 선택(`/api/providers`의 available만), 생성 버튼, 진행/오류 표시(`error.message` 그대로), 결과: 사양 JSON 접기 보기·경고·시도 이력·**MIDI 다운로드 링크**. 키·관리자 링크 노출 없음(푸터의 /admin 링크는 허용).
- 관리자: 최초 설정 화면(설정 토큰+비밀번호) → 로그인 → 공급자별 키 입력(비밀번호 입력창, 저장 후 값은 다시 보이지 않고 `…abcd`만), 모델 이름, 켜기/끄기, 연결 테스트 버튼(비용 발생 안내 문구), 제한값, `public_llm` 토글, 저장소 암호화 경고 배너.
- 접근성·반응형 기본 준수, 외부 CDN·폰트·스크립트 사용 금지(오프라인 동작). 모든 동적 텍스트는 `textContent`로 넣어 XSS를 막는다(필수).

## 7. 시험·품질 기준 (필수)
- 단언 있는 시험만. 백엔드: 인증(설정 1회성, 잘못된 토큰/비밀번호, CSRF 누락, 세션 만료), 키 비노출(모든 응답·로그에서 키 문자열 검색해 0건), 제한(속도·일일 상한·프롬프트 길이), `public_llm=false` 동작, MIDI TTL, 오류 형식.
- 프런트: 최소한 DOM 수준 시험 또는 API 계약 시험(브라우저 도구가 없으면 정적 점검 스크립트+수동 점검 목록을 PR에 적고 확인 못 한 것은 '미확인').
- CI(GitHub Actions): `pytest`, 비밀 문자열 스캔(간단한 패턴), 의존성 라이선스 목록 갱신 확인. 실제 LLM 호출 없음.
- 배포: `Dockerfile`(비루트 사용자, 데이터 디렉터리 볼륨), README에 실행법(`uvicorn`)·관리자 최초 설정·키 등록·비밀번호 분실 시 절차.

## 8. 라이선스
- 프로젝트 라이선스는 **PolyForm Noncommercial 1.0.0**(`LICENSE`, 원문은 polyformproject.org 에서 가져옴). 일반적인 OSI 오픈소스가 아니라 소스 공개(source-available)이며 법무 확인은 별도 과제다.
- 의존성은 `data/sources.csv`에 라이선스를 기록한다. 비상업 조건과 충돌할 수 있는 의존성(예: 상업 이용을 막는 모델 가중치)은 번들하지 않는다.

## 9. 구현 메모 (PR B, 2026-11-10 — 계약에서 비어 있던 부분을 구현하며 정한 것)
계약의 의미는 바꾸지 않았다. "구현 시 확정"으로 남겨 둔 항목과 계약에 없던 세부를 적는다.
- **진입점**: `uvicorn prompt2midi.api.app:app`. `app` 은 처음 접근할 때 만들어진다(모듈을 import 만 해서는 디렉터리·토큰이 생기지 않음). 시험·임베드용 `create_app(settings, adapters=None)`.
- **환경변수**: `P2M_DATA_DIR`(기본 `./instance`), `P2M_SECRET_KEY`(있으면 `secrets.json` 암호화), `P2M_MIDI_TTL`(초, 기본 3600), `P2M_TRUST_PROXY=1`(클라이언트 IP 를 `X-Forwarded-For` 의 **마지막** 항목으로 — 프록시 뒤에서만 켤 것. 꺼 두면 헤더는 무시되어 속도 제한을 헤더로 우회할 수 없다), `P2M_WEB_DIR`(화면 폴더, 기본 저장소의 `web/`). 키 자체를 받는 환경변수는 없다.
- **키 암호화**: `cryptography` 의 Fernet. 키는 `P2M_SECRET_KEY` 에서 scrypt(n=2^14, r=8, p=1, 무작위 솔트 16바이트)로 유도. 쓸 때마다 새 솔트. 평문 파일은 읽을 수 있고 다음 쓰기에서 암호화로 바뀐다. 암호화된 파일을 다른/없는 `P2M_SECRET_KEY` 로 열면 저장된 키가 "없는 것"으로 보이고(로그에 경고, 키 값은 로그에 없음) 관리자가 다시 입력하면 된다.
- **파일**: `admin.json`(scrypt 해시 n=2^14,r=8,p=1 + 솔트), `settings.json`, `secrets.json`, `usage.json`(그날 LLM 호출 수 — 재시작해도 하루 상한이 초기화되지 않게 저장). 전부 0600, 디렉터리 0700, 임시 파일에 쓴 뒤 교체.
- **세션**: 서버 메모리(재시작하면 모두 로그아웃). 쿠키 `p2m_session`, 12시간. 로그아웃은 CSRF 필요. 한 사람이 여러 세션을 가질 수 있고 각 세션의 CSRF 토큰은 서로 다르다.
- **오류 코드(관리자·공통, 계약 2절 코드에 추가)**: 400 `invalid_input`(검증 실패, `details:[{path,message}]`; 제출한 값은 되풀이하지 않음), 401 `unauthorized`(세션 없음/만료)·`invalid_credentials`, 403 `csrf_failed`·`invalid_setup_token`·`llm_disabled`, 404 `not_found`, 405 `method_not_allowed`, 409 `already_configured`·`not_configured`, 429 `rate_limited`(로그인/설정 실패 제한 포함)·`daily_cap_reached`, 500 `internal_error`(내용 없음).
- **로그인/설정 실패 제한**: IP 당 10분에 5회(설정 토큰 오입력도 같은 카운터). 실패마다 0.5초 지연. 성공하면 카운터 초기화.
- **속도 제한**: `/api/generate` 요청(검증 실패 포함)을 IP 당 분당 N회(기본 10)로 센다. 하루 LLM 상한은 **실제 LLM 호출(시도) 수**로 세며 UTC 날짜 기준, 실패한 시도도 센다. 상한 확인은 요청 시작 시 한 번이라 마지막 요청이 `max_retries` 만큼 넘길 수 있다. 규칙 공급자는 상한과 무관.
- **입력 범위**: 프롬프트 1..`max_prompt_chars`(관리자 설정, 1..2000, 기본 1000). 제한값 범위: `requests_per_minute` 1..600, `daily_llm_cap` 0..100000, `max_prompt_chars` 1..2000, `max_retries` 0..5. API 키 16..512자의 공백 없는 인쇄 가능 ASCII. 모델 이름 `[A-Za-z0-9._:/@+-]` 120자 이내. 숫자·불리언은 엄격 검사(`"5"`, `"yes"` 거부).
- **`default_provider`**: `public_llm=false` 인데 기본이 LLM 이면 조용히 규칙 공급자로 처리한다(명시적으로 LLM 을 요청하면 403 `llm_disabled`).
- **연결 테스트 응답**: `{"ok": bool, "latency_ms": int, "message": str}`(HTTP 200; 실패해도 200 이고 `ok:false`, 메시지는 키 제거됨). 요청은 `max_tokens` 64 의 최소 구조화 출력 1회.
- **보안 헤더**: CSP(`default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; …`), `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `/api/admin/*` 는 `Cache-Control: no-store`. `style-src-attr 'unsafe-inline'` 은 `web/admin.html` 의 인라인 `style` 속성 하나(로그아웃 줄) 때문이며, 그것을 CSS 클래스로 옮기면 지울 수 있다.
- **정적 파일**: `web/` 의 `*.html *.js *.css` 만 `/이름` 으로, `/` 는 `index.html`, `/admin` 은 `admin.html`. 그 밖의 파일(README 등)과 상위 경로는 404.
- **SDK 로거**: `anthropic`·`openai`·`httpx`·`httpx2`·`httpcore` 를 WARNING 으로 고정.
