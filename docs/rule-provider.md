# RuleProvider 해석 규칙 (조성·박자표·템포·장르)

- 작성: 조현석 · 2026-11-11 · 시험: `tests/test_rule_provider.py`, `tests/test_rule_korean.py`, `tests/test_translate.py`, `tests/test_rule_intent_path.py`
- 규칙 기반이라 키·네트워크가 필요 없고 같은 프롬프트는 같은 사양을 낸다. `RuleProvider.explain(prompt)` 로 무엇이 맞았고 무엇이 기본값이었는지 볼 수 있다.
- **소리에 대한 한계**: 장르·무드가 소리로 구분되는지는 **청취로 확인하지 않았다.** 아래 모든 매핑은 구조(유효 MIDI·결정성·규칙 우선순위)로만 시험했다.

## 0. 구조 (2026-11-11, Intent 경로)
```
문장 ─ translate(어휘 JSON + 조성·박자표·BPM 패턴) ─▶ Intent v1 ─ expander(instruments_mode="fill") ─▶ MusicSpec ─▶ MIDI
```
- **`src/prompt2midi/translate/`**: 문장에서 **추출만** 한다(없으면 null, 추측 없음; 모순은 `conflicts`, 정규화한 표현은 `raw`). 어휘는 `translate/lexicon/*.json`(`moods`·`instruments`·`genres`·`tempo`; 코드 없이 검토·수정 가능, 각 파일의 `_note` 참고). 조성·박자표·BPM 패턴은 `translate/keys.py`. 이 모듈은 expander·프리셋을 import 하지 않는다(시험으로 고정).
- **expander**(`docs/expander.md`): 모든 음악적 결정(기본값, 장르 프리셋, 악기 역할, 코드). RuleProvider 는 `fill` 모드(§5.1)를 쓴다.
- `providers/rule.py` 는 이 둘을 잇는 얇은 껍데기다. 옛 `rule.py` 의 어휘 표는 lexicon 으로, 숫자 표(무드·진행·모티프)는 `presets.py` 로, 키·박자표 패턴은 `translate/keys.py` 로 옮겼다(`find_key` 등 이름은 `rule.py` 에서 계속 import 가능).
- `explain(prompt)`: 예전 요약 키(`mood`, `key`, `tempo_bpm`, `time_signature`, `lead`, `bass`, `drums`, `defaults`, `words_seen`)에 **`intent`**(추출한 Intent), **`extraction`**(맞은 단어·출처), **`expander`**(`field/value/source/rule` 결정 목록)를 더했다.
- 출력 모양은 예전과 같다: 사양에 `seed` 없음(컴파일러가 내용에서 유도), 제목은 `"<Mood> <Tonic> <mode>"` 형식 그대로.

## 0.1 옛 코드와의 대응
| 옛 `rule.py` | 지금 |
|---|---|
| `MOODS` 의 단어 | `translate/lexicon/moods.json` (그대로) |
| `MOODS` 의 에너지·모드·BPM·모티프, `PROGRESSIONS`, `MOTIFS`, `DEFAULT_TONIC` | `presets.py` |
| `INSTRUMENT_WORDS`, `BASS_WORDS`, `DRUM_WORDS`, `LEAD_INSTRUMENTS` | `lexicon/instruments.json` (그대로) |
| `PAD_WORDS` | `pad`·`패드` 만 `synth_pad` 로 추출. `ensemble/orchestral/orchestra/오케스트라/ambient` 는 §5 참고 |
| `STYLE_WORDS` (드럼 스타일 단어) | `lexicon/genres.json` 의 장르 단어(bossa·electronic·jazz·blues·ballad·ambient·rock·pop). `slow/cinematic/shuffle` 은 enum 에 장르가 없어 빠짐(§5) |
| `TEMPO_WORDS` | `lexicon/tempo.json` (+ 한글 이탈리아어 표기 4개, `천천히`) |
| `find_key` / `find_time_signature` / 거짓 양성 규칙 | `translate/keys.py` (그대로) |
| 트랙·드럼·코드 결정 로직 | expander `fill` 모드 + 장르 프리셋 |

## 1. 조성(tonic + mode) 우선순위
1. **프롬프트에 적힌 명시 조성**(음 이름 + 모드). 여러 개면 **먼저 나온 것**.
2. **음 이름 없이 모드만 적힌 경우**(`도리안`, `dorian`, 단독 `단조`/`장조`) → 그 모드, 음은 모드의 기본 음(major C, minor A, dorian D, mixolydian G, lydian F, phrygian E, harmonic_minor A).
3. 아무것도 없으면 **무드 단어가 정한 모드** + 그 모드의 기본 음.
명시 조성은 음과 모드만 바꾼다. 에너지·템포·모티프·악기는 계속 무드가 정한다(시험으로 고정).

### 인식하는 한국어 표기
| 형태 | 예 | 결과 |
|---|---|---|
| 계이름 + 장조/단조 | 다장조, 라단조, 마장조, 사장조, 가단조, 나단조, 바장조 | C·D·E·G·A·B·F (가=A 나=B 다=C 라=D 마=E 바=F 사=G) |
| 올림/내림 + 계이름 | 올림바단조, 내림나장조, 내림 마 장조 | F# minor, Bb major, Eb major |
| 영문 음 이름 + 모드 | A단조, C장조, F샵 단조, B플랫 장조, Bb장조, 내림B장조, a단조 | A minor, C major, F# minor, Bb major … |
| 영문/계이름 + 교회 선법·화성단음계 | D 도리안, 라 도리안, G 믹솔리디안, F 리디안, E 프리지안, A 화성단조, A 하모닉 마이너 | 해당 모드 |
- 조사가 붙어도 된다(`C장조에서`, `A단조로`). 단어 안쪽의 음절(`연마단조`)은 계이름으로 읽지 않는다. `단조롭다/단조로운/단조로워/단조로움` 은 모드가 아니다.
- **거짓 양성 방지(2026-11-11)**: `장조/단조`(와 선법 이름) 바로 뒤에 한글이 이어지면 **허용 목록**(에서·으로·로서·로·의·는·은·를·을·가·이·도·와·과·곡·풍·처럼·에·만·까지·부터·보다·인·이다·입니다·이라·라서·라·이고·고·며·면·인데·께·스타일·느낌, 겹쳐 쓰기 가능)으로만 이어질 때 모드로 읽는다. 그래서 `사장조직`, `마장조선시대`, `장조림`, `단조건` 은 조성이 아니다. 한 음절 계이름(가나다라마바사)이나 소문자 영문 한 글자와 `장조/단조` 사이에 공백이 있으면(`가 장조 심`, `a 단조`) 조사·관사로 보고 조성으로 읽지 않는다(선법 이름 `라 도리안` 과 `올림/내림`이 붙은 `내림 나 장조`, 대문자 영문 `A 단조` 는 공백이 있어도 읽는다).
- **알려진 한계**: 계이름과 `장조/단조` 가 붙어 있고 바로 뒤에 공백이 오는 `사장조 직원`(사장 + 조 직원)은 `라단조 피아노` 와 모양이 같아 **G major 로 읽는다**(사전 없이는 구별 불가). 시험으로 고정(`test_known_limitation_…`).
- 우리 음 이름 목록에 없는 철자(`내림다장조` = Cb)는 **추측하지 않고 무시**하고 2·3번 규칙으로 간다.
- 영어 표기(`A minor`, `B-flat major`, `F# minor`, `F-sharp minor`)는 그대로 지원한다.

## 2. 박자표
| 표기 | 해석 |
|---|---|
| `3/4`, `6/8 박자` 등 | 그대로 |
| `N분의 M박자` (`4분의 3박자`, `사분의 삼박자`, `팔분의 육박자`) | **M/N** (3/4, 6/8). 아라비아·한자어 숫자(이~구) 모두 |
| `M박자` (`3박자`, `삼박자`) | 2→2/4, 3→3/4, 4→4/4, 5→5/4 (그 외는 모호해서 해석 안 함) |
- 허용 집합(2/4 3/4 4/4 5/4 6/8 7/8)에 없으면(`9분의 8박자`, `6박자`…) 4/4 로 되돌린다. 먼저 나온 표기가 우선.

## 3. 템포
`N BPM`(40~**200**; 201~220 은 200 으로 줄이고 `conflicts`·`raw` 에 남김. 옛 규칙은 220 까지 받았으나 Intent v1 범위가 40~200) > 템포 말(`아주 느린` 55 < `느리게/느린` 66 < `보통` 96 < `빠르게/빠른` 140 …, 영어 `slow/adagio/allegro/fast…`) > 무드 기본값. 한국어로 적은 이탈리아어 용어(아다지오 66·모데라토 96·안단테 96·알레그로 120)와 `천천히`(66)를 읽는다(2026-11-11, PR #19 의 xfail 해소). 숫자와 말이 크게 어긋나면(25% 초과) `conflicts` 에 남기고 숫자를 쓴다.

## 4. 장르 (2026-11-11부터 Intent.genre 로 추출, 프리셋은 expander)
장르 단어는 `lexicon/genres.json` 의 **우선순위 순서**로 `Intent.genre`(최대 3개)에 들어가고, expander 가 **첫 번째 장르**의 프리셋(드럼 스타일·진행·악기·에너지, `docs/expander.md` §4)을 적용한다. 그래서 `비 오는 날 창가에서 듣는 재즈`, `재즈`, `보사노바 기타` 처럼 드럼을 말하지 않아도 장르가 사양에 나타난다(PR #19 의 xfail 7개가 통과 시험이 됨).
- 단어는 옛 드럼 스타일 표의 것 그대로(bossa·samba, dance·electronic·edm·house·techno·disco, jazz·swing, blues, ballad, ambient, rock·punk·metal, pop, 한글 표기)와 옛 무드 표의 `lo-fi/lofi`(lofi 장르). **새 단어는 추가하지 않았다**(힙합 단어만 예외: 아래). `classical` 은 옛 표에 단어가 없어 **추출되지 않는다**.
- **hiphop**: 합의된 genre enum 밖이라 `genre` 는 비우고 원문을 `raw.genre`("hiphop (not in the genre enum): 힙합")에 남긴다(단어: hiphop, hip-hop, hip hop, 힙합).
- 우선순위 순서: bossa_nova > electronic > jazz > blues > ballad > ambient > lofi > rock > pop(옛 드럼 스타일 표의 순서). 같은 문장에 두 장르가 있으면 앞선 쪽이 이긴다(`relaxing lo-fi jazz` → jazz).
- 장르 프리셋이 `mode`·에너지를 정하는 장르(pop=major, rock .8, ballad .3·70BPM, ambient .1, lofi .25)는 **무드보다 우선**한다(`A sad pop song` 은 이제 major).

## 5. 옛 동작에서 의도적으로 바뀐 것 (Intent 경로 전환, 2026-11-11)
| 변화 | 이유 |
|---|---|
| 장르 단어가 사양에 반영됨(드럼 트랙·7th 코드·에너지·장르 악기) | 합의된 구조(장르는 Intent 필드, 프리셋은 expander). 옛 규칙은 드럼이 이미 요청됐을 때만 스타일을 골랐음 |
| `at 220 BPM` → 200 | Intent v1 의 `tempo_bpm` 범위가 40~200(합의). 220 은 `conflicts` 에 기록 |
| 한글 이탈리아어 템포 용어·`천천히` 를 읽음 | 요청된 어휘 추가(PR #19 의 xfail) |
| `slow`·`cinematic` 이 드럼 스타일을 고르지 않음 | 옛 드럼 스타일 단어였지만 genre enum 에 대응 장르가 없음. 드럼 스타일은 에너지(< 0.35 half_time)를 따름 |
| `orchestral`·`ensemble`·`orchestra`·`오케스트라` 단독이 strings pad 를 만들지 않음 | 옛 'pad wanted' 단어였지만 악기 이름이 아님(새 단어를 만들지 않기로 함). `strings`·`현악`·`string ensemble` 은 그대로 strings pad |
| `ambient` 는 무드(meditative) 와 장르(ambient) 둘 다 | 단어 하나가 두 필드를 채움. ambient 프리셋은 가능성표대로 half_time 드럼을 가짐(= 옛 출력에 없던 드럼·베이스가 생김) |
| 같은 단어가 여러 번 나온 두 조성 중 첫 번째만 쓰고 `conflicts` 에 남김 | Intent 의 모순 규칙(옛 규칙은 조용히 첫 번째) |
바뀌지 않아야 하는 출력은 시험으로 고정했다: 옛 RuleProvider(e50d516)의 MIDI 해시 120개(`tests/fixtures/legacy_unchanged.json`) + 연구 1팀 영어 10개 중 장르 단어가 없는 6개(1·2·3·5·7·9)의 해시. 장르 단어가 있는 4개(4 rock, 6 lo-fi jazz, 8 ballad, 10 ambient)는 위 이유로 바뀌었고 어떻게 바뀌었는지 `tests/test_rule_intent_path.py` 에 적어 두었다.

## 6. 하지 않은 것
- **`왈츠 → 3/4`**: 하지 않았다(xfail 유지). 장르 단어에서 박자를 추론하는 것은 추출이 아니라 음악적 결정(expander 몫)이고, `waltz` 는 합의된 genre enum 에 없다. 필요하면 enum 합의 뒤 expander 프리셋으로 넣는다.
- 한 문장에 여러 무드가 있어도 **가장 많이 맞은 무드 하나**만 `Intent.mood` 에 넣는다(옛 규칙 유지, 나머지는 `raw.mood`). 여러 무드를 섞는 규칙은 평가 후 정한다.
- `bars`·`chords` 는 추출하지 않는다(옛 규칙에도 없었음).

