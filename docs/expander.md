# Expander — Intent v1 → MusicSpec (결정적)

- 작성: 조현석 · 2026-11-11 · 코드: `src/prompt2midi/intent.py`, `src/prompt2midi/expander/`, 공용 프리셋 `src/prompt2midi/presets.py` · 시험: `tests/test_intent.py`, `tests/test_expander.py`
- 근거: `docs/프롬프트투미디/2단계-계획서-합의안.md`(2층 구조), `intent-컴파일러-가능성표.md`(4절 규칙, 2절 장르 표). 연구 1팀이 enum 에 동의한 범위: dreamy·hiphop 은 이번 enum 에 **없음**.
- **소리에 대한 한계(먼저)**: 이 문서의 모든 매핑(장르→드럼·진행·악기, 무드→모드·템포·에너지)은 코드와 MIDI 구조(유효성·결정성)로만 확인했고 **청취로 확인하지 않았다.** "재즈답게 들리는가" 같은 소리 차이는 미확인이다.

## 1. 층 구조
```
문장 ─(규칙 공급자 / LLM)→ Intent v1 ─(expander: 결정적 코드)→ MusicSpec ─(compiler)→ MIDI
```
공급자는 **추출만** 한다(없으면 null, 모순은 `conflicts`, enum 밖 표현은 가장 가까운 enum + `raw`). 조성·코드·드럼·악기 배치 같은 음악적 결정은 모두 expander 가 한다. **이번 PR 에서는 RuleProvider 를 Intent 경로로 바꾸지 않았다**(다음 단계). 대신 무드 표·진행·모티프·코드 스펠링은 `presets.py` 하나로 합쳐 RuleProvider 와 expander 가 같은 정의를 쓴다(복사 없음, 시험이 `is` 로 확인).

## 2. Intent v1 (`schemas/intent.v1.schema.json`)
| 필드 | 값 | 비고 |
|---|---|---|
| `tonic` | 17종 표기(C C# Db D D# Eb E F F# Gb G G# Ab A A# Bb B) \| null | |
| `mode` | major minor dorian mixolydian lydian phrygian harmonic_minor \| null | |
| `tempo_bpm` | 정수 40~200 \| null | 문자열·실수·불리언 거부 |
| `time_signature` | "2/4" "3/4" "4/4" "5/4" "6/8" "7/8" \| null | |
| `bars` | 정수 1~64 \| null | 총 마디 수 |
| `mood` | enum 리스트(≤3, 중복 불가): calm relaxing sad dark happy playful energetic epic romantic meditative | `dreamy` 없음 |
| `genre` | enum 리스트(≤3): pop rock jazz blues ballad bossa_nova electronic ambient classical lofi | `hiphop` 없음 |
| `instruments` | enum 리스트(≤8): MusicSpec 악기 17종 + `drums` | |
| `chords` | 코드 이름 리스트(1~16, 품질 10종) \| null | |
| `conflicts` | `[{fields:[필드명…], reason}]` (≤8) | |
| `raw` | 필드별 원문(닫힌 객체, 값은 문자열 \| null) | 자유 형식 map 이 아니라 **필드마다 선택 문자열 하나**: 두 공급자가 구조화 출력에서 열린 객체(`additionalProperties`≠false)를 거부하기 때문 |
- 알 수 없는 필드·enum 밖 값은 거부. JSON Schema 는 모든 필드를 required 로 내보낸다(OpenAI strict 요구; 없으면 null/`[]`). Python 쪽은 필드를 생략해도 된다.
- 두 공급자 제약(`docs/providers.md`)은 기존 `providers/schema.py` 변환으로 충족되는지 시험한다: Anthropic(숫자 범위·maxItems 는 설명으로 옮겨지고 최종 검증은 우리 몫, 선택 파라미터 ≤24, 유니온 ≤16), OpenAI(전부 required, 속성 ≤5000, 깊이 ≤10).

## 3. 값 결정 순서 (가능성표 4절)
**명시(Intent) > 장르 프리셋 > 무드 프리셋 > 전역 기본값.** 같은 Intent 는 항상 같은 MusicSpec(시드는 Intent 의 **음악 필드**에서 유도 — `conflicts`·`raw` 는 메타데이터라 시드에 안 들어감). 모든 결정은 `Expansion.explain`(`field/value/source/rule`)에 남는다.
| 값 | 순서 |
|---|---|
| mode | 명시 → 장르 mode(pop·blues=major) → 무드 선호 모드 → major |
| tonic | 명시 → 모드의 기본 음(major C, minor A, dorian D, mixolydian G, lydian F, phrygian E, harmonic_minor A) |
| tempo_bpm | 명시 → 장르 bpm(ballad 70) → 무드 bpm → (무드 없음=calm) 72 |
| time_signature | 명시 → 4/4 |
| 에너지 | 장르 에너지(rock .8, ballad .3, ambient .1, lofi .25) → 무드 에너지(여러 개면 평균) → calm .25 |
| 코드 | 명시 → 장르 진행(+7th) → 모드별 기본 진행(`presets.PROGRESSIONS`) |
| bars | 명시 → 코드 명시 시 `max(12, 3×코드 수)` → 12 |
| 모티프 | 무드 프리셋(`presets.MOTIFS`) → 멜로디 악기 음역에 안 맞으면 좁은 `arch_slow` |
- **여러 무드**: 첫 번째가 모드·템포·모티프를 정하고 에너지는 전체 평균. **여러 장르**: 첫 번째만 쓰고 나머지는 explain 에 "무시"로 남긴다.
- 무드 없음 = 전역 기본 무드 `calm`(major, 72, 에너지 .25).
- **형식**: 총 마디 수 B 를 A·B·A2 세 섹션으로 `[(B+2)//3, (B+1)//3, B//3]` 로 나눈다(B≤3 이면 한 섹션; 12→4·4·4). 섹션 에너지는 A=e, B=min(1, e+0.2), A2=e. 코드는 A·A2 가 같은 진행, B 는 변형.
- **유일한 예외**: 명시한 `bars` 가 300초 한도를 넘으면(예: 40 BPM 4/4 에서 64마디=384초) 한도 안의 최대 마디 수로 줄이고 explain 에 `clamped` 로 남긴다.
- **섹션 수 제한 때문에 `chords` 가 더 길어도** 섹션 안에서 마디마다 순환한다(명시 코드가 길면 위 bars 규칙이 섹션 길이를 코드 수에 맞춘다).

## 4. 장르 프리셋 (가능성표 2절 그대로 — 표에 적힌 것만 정함)
| genre | 드럼 스타일 | 진행·악기·에너지 (표의 내용) | 판정(표) |
|---|---|---|---|
| `pop` | `rock_basic` | major, I–V–vi–IV | 근사 |
| `rock` | `rock_basic` | 에너지 .8, 멜로디 steel_guitar(일렉 기타), 베이스 electric_bass | 근사 |
| `jazz` | `shuffle` | 7th 코드(`7`·`m7`·`maj7`·`m7b5`) 진행 ii–V–I–vi / IV–vii–iii–vi, 멜로디 piano | 근사 |
| `blues` | `shuffle` | major, 모든 코드를 도미넌트 7(`C7 F7 C7 G7`, 조성 밖 7th 는 **경고**로만 알림) | 근사 |
| `ballad` | `half_time` | 70 BPM, 에너지 .3, 멜로디 piano + 패드 strings | 가능 |
| `bossa_nova` | `bossa` | 7th 코드 진행 ii–V–I–vi / I–vi–ii–V | 가능 |
| `electronic` | `four_on_floor` | 멜로디 synth_lead, 베이스 synth_bass | 근사 |
| `ambient` | `half_time` | 에너지 .1, 패드 synth_pad(패드 중심) | 가능 |
| `classical` | (드럼 트랙 없음) | 멜로디 piano + 패드 strings | 근사 |
| `lofi` | `half_time` | 에너지 .25, 멜로디 electric_piano, 7th 코드 | 근사 |
- 표에 없는 값(예: pop 의 템포, bossa 의 악기)은 프리셋이 정하지 않고 무드 프리셋·전역 기본값이 채운다. 숫자 중 표에 직접 없는 것(ballad 70 BPM 은 표의 "느린 템포"를, 장르별 에너지 값은 "높게/낮은/매우 낮은"을 숫자로 옮긴 **제 판단**)은 연구 1팀 검토 대상이다.
- 무드 프리셋은 기존 `rule.py` 의 `MOODS` 표(10종: 에너지·선호 모드·기본 BPM·모티프)를 그대로 쓴다.

## 4.1 무드 프리셋 (`presets.MOODS`, RuleProvider 와 같은 표)
| mood | 에너지 | 선호 모드 | 기본 BPM | 모티프 |
|---|---|---|---|---|
| `calm` | 0.25 | major | 72 | `arch_slow` |
| `relaxing` | 0.3 | dorian | 85 | `arch_slow` |
| `sad` | 0.3 | minor | 62 | `fall` |
| `dark` | 0.5 | phrygian | 80 | `fall` |
| `happy` | 0.7 | major | 120 | `bounce` |
| `playful` | 0.7 | mixolydian | 128 | `bounce` |
| `energetic` | 0.9 | minor | 140 | `drive` |
| `epic` | 0.8 | major | 100 | `rise` |
| `romantic` | 0.4 | major | 90 | `arch_slow` |
| `meditative` | 0.15 | lydian | 58 | `arch_slow` |
- 값은 코드의 표를 그대로 옮긴 것이며 시험(`test_the_docs_list…`)이 문서와 코드가 어긋나면 실패한다. 음계·템포가 그 무드로 *들리는지*는 확인하지 않았다.

## 5. 악기 → 역할 배치
**명시한 `instruments` 는 앙상블 전체**다: 나열한 악기만 연주하고 장르·무드의 기본 악기·베이스·드럼은 채우지 않는다(`drums` 를 나열했을 때만 드럼 트랙, 스타일은 장르 → 에너지 순). 나열하지 않으면(null/빈 목록) 아래 기본값.
- **명시 목록의 배치 규칙**(나열 순서대로, 역할은 한 번씩):
  1. `drums` → 드럼 트랙.
  2. 베이스 악기(upright_bass electric_bass synth_bass) → bass(이미 찼으면 무시).
  3. `synth_pad` → pad(이미 찼으면 무시).
  4. 그 밖의 악기 → 적합한 역할 중 **비어 있는 첫 번째**: melody → chords → pad.
     - melody 적합: piano electric_piano nylon_guitar steel_guitar violin strings trumpet sax flute clarinet marimba synth_lead organ
     - chords 적합: piano electric_piano organ nylon_guitar steel_guitar marimba strings violin
     - pad 적합: strings organ synth_pad
     - 어디에도 못 들어가면 무시(explain 의 `instruments.ignored`).
  예: `[piano, strings]` → melody piano, chords strings / `[strings, piano]` → melody strings, chords piano / `[flute, trumpet, sax]` → melody flute(나머지 무시) / `[organ, piano, strings]` → melody organ, chords piano, pad strings.
- **기본값(목록 없음)**: 장르 악기 → melody 기본 piano; pad 는 무드가 meditative·epic 이면 synth_pad; chords 는 pad 가 없을 때 electric_piano(멜로디가 piano 일 때) 또는 piano; bass 는 드럼이 있거나 에너지 ≥ 0.4 이면 electric_bass; 드럼은 장르가 정하거나(classical 은 없음), 장르가 없으면 에너지 ≥ 0.7. 드럼 스타일은 장르 → 에너지(< 0.35 half_time, 그 외 rock_basic).

## 5.1 `instruments_mode="fill"` (RuleProvider 용)
`expand(intent, instruments_mode="fill")` 은 명시한 악기를 **앙상블 전체가 아니라 자기 역할에만** 쓰고, 나머지 역할은 장르·무드·기본값으로 채운다. 기본값(`"ensemble"`)과 위 5절은 그대로다. 왜 필요한가: 앙상블 의미로는 "calm piano piece" 가 코드 트랙 없는 피아노 한 대가 되어 버려 기존 RuleProvider 의 출력(피아노 + 전자 피아노 코드)이 크게 달라진다.
- 배치(`assign_instruments_fill`, 옛 RuleProvider 와 같은 결과를 내도록): melody = 나열된 것 중 처음 melody 가 가능한 악기(**오르간은 melody 가 되지 못함**), chords = 남은 것 중 처음 chords 에 맞는 악기(오르간 가능, `strings` 는 제외), pad = `strings` 가 있으면 strings(멜로디를 겸해도 pad 도 맡음 — 옛 규칙의 동작), 없으면 `synth_pad`, bass = 처음 베이스 악기, drums = `drums` 가 있으면.
- 채움: 장르 악기 → melody 기본 piano → pad(무드 meditative/epic 이면 synth_pad) → chords(pad 가 없고 에너지 < 0.7 일 때 electric_piano, 멜로디가 piano 가 아니면 piano) → drums(나열했거나 장르가 드럼을 가짐, 장르가 없으면 에너지 ≥ 0.7) → bass(드럼이 있거나 에너지 ≥ 0.4 이면 electric_bass).
- 결정성·유효성은 ensemble 과 같은 시험으로 확인(무드 × 장르 100 조합, 악기 18종, 악기 쌍).

## 6. 보장하는 것과 못 하는 것
- 생성된 MusicSpec 은 `validate_spec`·`compile_spec`·`check_midi`(이슈 0건)를 통과한다. 시험: 무드 10 × 장르 10 조합 전부, 무드 단독·장르 단독, 악기 18종 단독 앙상블, 좁은 음역 악기 8종 × 조성 17 × 모드 7, 박자표 6 × 템포 40/200 × 마디 1/64, null 투성이·꽉 찬 Intent.
- 멜로디 악기의 음역에 모티프가 안 맞는 키에서는 좁은 `arch_slow` 모티프로 바꾼다(explain 에 기록).
- **못 하는 것**: 청취 평가(장르·무드가 소리로 구분되는지), 장르 고유의 리듬·음색(근사 항목), 멜로디 변주·곡 구조 변화(컴파일러 한계, 가능성표 3절).
