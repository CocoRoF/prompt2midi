# LLM 공급자 (Anthropic · OpenAI) — 확인 기록과 구현 메모

- 안건 c5b5fa12 · 작성: 조현석 · 확인일 **2026-11-10** · 계약: `docs/api-and-admin.md` 5절
- 원칙: 파라미터·구조화 출력 방식은 **공식 문서 원문**을 열어 확인했다. 모델 이름은 코드에 없다(관리자 패널의 `model` 설정). 이번 작업에서 **실제 API 호출은 하지 않았다**(키 없음). 시험은 실제 SDK를 쓰되 HTTP 계층을 메모리 전송(`httpx2.MockTransport`)으로 바꿔 돌린다.
- 표기: **확인** = 아래 URL 원문에서 읽음 / **SDK 확인** = 설치한 SDK 코드·시그니처로 확인 / **미확인**.

## 1. Anthropic (Messages API)
| 항목 | 내용 | 근거 |
|---|---|---|
| 구조화 출력 방식 | 요청에 `output_config={"format": {"type": "json_schema", "schema": {...}}}`. 응답은 text 블록의 JSON | 확인: https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| 베타 헤더 | 필요 없음(`output_format` 구형 파라미터는 폐기, `output_config.format` 사용) | 확인: 같은 문서 "Migrating from the beta" |
| 요청 파라미터 | `model`, `max_tokens`(필수), `system`(최상위 파라미터), `messages`, `output_config` | 확인: https://platform.claude.com/docs/en/api/messages/create , https://platform.claude.com/docs/en/api/python/messages/create |
| 응답 `stop_reason` | `"refusal"`(200으로 오며 스키마와 안 맞을 수 있음), `"max_tokens"`(출력이 잘려 스키마와 안 맞을 수 있음) | 확인: 구조화 출력 문서 "Invalid outputs" |
| 스키마 제약 | 지원: 기본 타입, enum, const, anyOf, `$ref`/`$defs`, `additionalProperties:false`(필수), `minItems` 0·1만, 문자열 포맷 일부, 정규식(역참조·lookaround·`\b` 불가). **미지원**: `minimum/maximum/multipleOf`, `minLength/maxLength`, `minItems`>1, `maxItems` 등 | 확인: 같은 문서 "JSON Schema limitations" |
| 복잡도 한도 | strict 도구 20개, **선택 파라미터 합계 24개**, **유니온 타입 파라미터 합계 16개**, 컴파일 타임아웃 180초 | 확인: 같은 문서 "Schema complexity limits" |
| SDK 도우미 | `anthropic.transform_schema()` — 미지원 제약을 설명 문자열로 옮기고 `additionalProperties:false` 추가 | 확인: 같은 문서 / SDK 확인: anthropic 1.4.0 |
| 기타 | 첫 요청은 문법 컴파일로 느림(24시간 캐시), 메시지 prefill·citations와 비호환, enum 값 대소문자가 정확히 보장되지 않음 | 확인: 같은 문서 |
| 모델 이름 | 문서 예시에 `claude-opus-5-5` 등이 나오지만 **코드에 넣지 않음**. 가격표(https://platform.claude.com/docs/en/about-claude/pricing , 2026-11-09 열람)에 현재 모델이 나열됨. 구조화 출력의 지원 모델: 문서가 "All models"라고 표기 | 확인 |

구현: `providers/adapters.py` `AnthropicSDKAdapter`, 스키마 변환 `providers/schema.py` `schema_for_anthropic`. 우리 스키마는 선택 파라미터 9개·유니온 5개로 한도 안(시험이 단언).

## 2. OpenAI (Responses API)
| 항목 | 내용 | 근거 |
|---|---|---|
| 구조화 출력 방식 | Responses API: `text={"format": {"type": "json_schema", "name": ..., "strict": true, "schema": {...}}}`. (Chat Completions 는 `response_format`) 응답 본문은 `output_text` | 확인: https://developers.openai.com/api/docs/guides/structured-outputs |
| 요청 파라미터 | `model`, `instructions`(시스템/개발자 메시지), `input`, `text`, `max_output_tokens`, `store` | 확인: https://developers.openai.com/api/reference/resources/responses/methods/create , SDK 확인: openai 3.8.0 |
| 스키마 제약 | **모든 필드 required**(선택 필드는 `null`과의 유니온으로 흉내), `additionalProperties:false`(필수), 루트는 `anyOf` 없는 object, 지원 속성: `pattern`·`format`·`minimum/maximum`·`multipleOf`·`minItems/maxItems` 등, `$defs`/`$ref`·재귀 지원 | 확인: 같은 가이드 "Supported schemas" |
| 미지원 | `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if/then/else` | 확인: 같은 가이드 |
| 한도 | 객체 속성 5000개, 중첩 10단계, 속성명·enum 값 등 문자열 길이 합 120,000자, enum 값 1000개 | 확인: 같은 가이드 |
| 거절·중단 | 응답에 `refusal`(Responses 에서는 output content 의 `type:"refusal"`), 중단은 `status:"incomplete"` + `incomplete_details.reason`(`max_output_tokens` 등) | 확인: 같은 가이드(refusals 절)·API 레퍼런스 `incomplete_details` |
| 모델 이름 | 가이드 예시에 `gpt-6-astra` 등이 나오지만 **코드에 넣지 않음**. "gpt-4o 이후 모델에서 구조화 출력 지원" | 확인: 같은 가이드 "Supported models" |

구현: `OpenAISDKAdapter`, `schema_for_openai`(필수화 + nullable 유니온 + 튜플→고정 길이 배열 + `default` 제거), 응답은 `strip_nulls`로 null 키를 지운 뒤 우리 검증을 통과해야 한다.

## 2.5 시험한 SDK 버전 범위와 상한 (2026-11-10)
| SDK | 시험한 버전 | 최신(PyPI, 같은 날) | 설치 범위 |
|---|---|---|---|
| anthropic | **1.4.0**, **2.7.0** | 2.7.0 | `>=1.4,<3` |
| openai | **3.8.0**, **3.49.0** | 3.49.0 | `>=3.8,<4` |
- 같은 시험(`tests/`, 489개)을 Python 3.11·3.12·3.13·3.14 와 위 두 조합(anthropic 1.4.0+openai 3.8.0 / anthropic 2.7.0+openai 3.49.0)에서 모두 통과시켰다(`uv run --isolated --python 3.x --with-editable ".[dev]" --with anthropic==… --with openai==… python -m pytest -q`).
- **관찰된 SDK 차이**: anthropic 2.x 는 API 키를 `x-api-key` 가 아니라 `authorization: Bearer …` 헤더로 보낸다(1.x 는 `x-api-key`). 그래서 시험은 "키가 인증 헤더 하나에만 있고 다른 헤더·본문에는 없다"를 두 형태 모두에서 단언한다(`tests/test_llm_providers.py::sent_key`). 어댑터가 쓰는 호출(`messages.create(model, max_tokens, system, messages, output_config, timeout)`, `responses.create(model, instructions, input, text, max_output_tokens, store, timeout)`, 클라이언트 생성자 `api_key/max_retries/http_client/base_url`, `anthropic.transform_schema`)은 시그니처 검사로 두 조합 모두에 있음을 확인했다.
- **상한을 둔 이유**: 위에서 보듯 주(major) 버전이 바뀌면 동작이 달라진다(인증 헤더). 다음 주 버전(anthropic 3, openai 4)은 시험해 보지 않았고 CI 가 설치 시점마다 최신을 받으므로, 검증하지 않은 주 버전이 자동으로 들어와 배포가 깨지거나 키 처리 가정이 어긋나는 것을 막으려고 상한을 둔다. 하한은 시험한 가장 낮은 버전이다. 사이 버전(예: anthropic 1.5~1.x, 2.0~2.6)은 **개별로 시험하지 않았다**(같은 주 버전 안이면 호환이라는 가정). 새 주 버전이 나오면 이 절의 시험을 다시 돌리고 상한을 올린다.
- 이 절의 확인은 모두 메모리 전송·시그니처 수준이며 실제 API 호출은 하지 않았다.

## 3. 공통 설계
- **입력은 신뢰하지 않는다**: 시스템 프롬프트(`providers/prompts.py`)와 사용자 설명을 분리하고, 사용자 글은 `<user_prompt>…</user_prompt>` 안에 넣는다(안의 닫는 태그는 제거). 모델 출력은 항상 `validate_spec` → `compile_spec` → `check_midi` 를 통과해야 MIDI가 된다.
- **재시도·피드백**: SDK 자체 재시도는 끈다(`max_retries=0`). 재시도는 파이프라인이 오류 목록을 `<errors>` JSON으로 되돌려 한다.
- **키 처리**: 키는 요청마다 `key_source()`에서 읽어 클라이언트를 만든다(저장 즉시 반영). 키가 없거나 `model`이 비면 요청 자체를 보내지 않는다. 모든 SDK 예외는 `mask_secrets`(전체 키 + 키의 6자 이상 조각 + `Bearer`/`x-api-key` 형태 + `sk-…` 토큰)를 거쳐 `ProviderError`로 바뀌고, 원인 예외는 붙이지 않는다. 키는 요청 헤더에만 있고 본문에 없다는 것도 시험이 단언한다.
- **SDK 로거**: `anthropic`·`openai`·`httpx2` 로거를 DEBUG 로 올리면 요청 정보가 남을 수 있다. 앱(PR B)은 이 로거들을 WARNING 으로 고정한다.

## 4. 미확인 / 한계
- **실제 API 호출은 하지 않았다.** 요청이 실제 서버에서 받아들여지는지(특히 우리 스키마가 두 공급자의 컴파일러를 통과하는지)는 미확인. 규칙과 한도는 문서로 맞췄고 구조 시험으로 확인했을 뿐이다. `P2M_LIVE=1`+관리자가 등록한 키로 하는 실호출 시험은 아직 없다(계약 5절의 후속).
- 두 공급자의 현재 모델 목록·가격·속도 제한·타임아웃 권장값·오류 본문 형식은 이번에 확인하지 않았다(가격표는 참고로만 열람).
- OpenAI Responses 의 `refusal` 파트와 `incomplete` 상태 처리는 문서 설명과 SDK 모델로 구현했고 메모리 전송 시험으로만 확인했다.
- Anthropic enum 값 대소문자 불일치 가능성(문서 명시)은 우리 enum 이 공백 없는 소문자·음이름이라 영향이 작다고 보지만 실측하지 않았다. 어긋나면 검증이 거부하고 재시도 피드백으로 돌아간다.
- 우리 스키마의 `register`(튜플)는 두 공급자 모두 "길이 2 배열"로 바뀌며 길이·범위는 우리 검증이 최종 확인한다.
