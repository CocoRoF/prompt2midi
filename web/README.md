# web/

빌드 없는 정적 화면(외부 CDN·폰트 없음). 백엔드(`prompt2midi.api.app`)가 같은 프로세스에서 서빙한다.

| 파일 | 내용 |
|---|---|
| `index.html`, `app.js` | 공개 화면: 프롬프트 → MIDI 생성·다운로드 |
| `admin.html`, `admin.js` | 관리자 패널: 최초 설정·로그인·LLM 키 등록·제한값 |
| `common.js`, `app.css` | 공통 도우미(textContent 만 사용)·스타일 |

계약: `docs/api-and-admin.md` 2~4, 6절. 동적 텍스트는 항상 `textContent` 로 넣는다(XSS 방지).
