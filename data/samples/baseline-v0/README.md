# baseline-v0 샘플 MIDI

- 생성: `python tools/make_baseline_samples.py` · 생성 시점 코드 커밋 `2ad9e34` (RuleProvider, 폴백 없음, 시드는 사양의 것)
- 프롬프트: 연구 1팀 영어 10개(`data/eval_prompts_research1.json`, id `r01`~`r10`) + 개발용 smoke 세트(`tests/fixtures/smoke_set.json`)에서 장르·무드가 고르게 섞이게 6개. 파일명 = 프롬프트 id.
- 보류 세트·외부 데이터·LLM·키 없음. 이 MIDI 는 **동결된 기준선**이다: 테스트는 다시 생성해 비교하지 않는다. 규칙·컴파일러를 바꿔 다시 만들 때는 `baseline-v1` 처럼 새 폴더로 둔다.
- `set.json` 은 같은 목록을 평가 세트 형식으로 적은 것이다: `python -m prompt2midi.evalkit data/samples/baseline-v0/set.json --provider rule`.
- 이 파일들의 표현력 대리 지표 값과 해석: `docs/baseline-expressiveness-v0.md` (값은 음악 품질이 아니다).

| id | 언어 | 프롬프트 | MIDI sha256(앞 8자) | 바이트 |
|---|---|---|---|---|
| `r01` | en | A calm and relaxing piano piece in C major with a 4/4 time signature, moving at a slow Adagio tempo of about 70 BPM. It evokes peace and serenity. | `5837a935` | 883 |
| `r02` | en | A sad and melancholic song for piano and strings in A minor, 4/4 time signature, at a slow tempo of about 60 BPM, with a deeply emotional feel. | `a0f6731f` | 735 |
| `r03` | en | A cheerful and upbeat pop song with acoustic guitar, bass and drums in G major, 4/4 time signature, at a lively tempo of about 120 BPM, evoking happiness. | `8533f3d8` | 2373 |
| `r04` | en | An energetic rock track featuring electric guitar, electric bass and drums in E minor, 4/4 time signature, at a fast tempo of about 140 BPM, with a powerful and intense mood. | `d9eedeca` | 2377 |
| `r05` | en | A dark and mysterious cinematic piece for string ensemble and low brass in D minor, 4/4 time signature, at a moderate tempo of about 80 BPM, creating tension. | `69624914` | 1191 |
| `r06` | en | A relaxing lo-fi jazz piece with electric piano, soft bass and light drums in F major, 4/4 time signature, at a laid-back tempo of about 85 BPM. | `c611d2b4` | 2411 |
| `r07` | en | An epic and heroic orchestral piece with strings, brass and timpani in D major, 4/4 time signature, at a tempo of about 100 BPM, with a triumphant mood. | `bcc94b5c` | 2322 |
| `r08` | en | A romantic and tender ballad for piano and violin in B-flat major, 3/4 time signature, at a gentle tempo of about 90 BPM, full of warmth and love. | `31296d4b` | 2054 |
| `r09` | en | A playful and bouncy electronic dance track with synth lead, synth bass and drums in C major, 4/4 time signature, at a fast tempo of about 132 BPM. | `3ed90aaa` | 2231 |
| `r10` | en | A meditative ambient composition with soft pads, harp and flute in G minor, 4/4 time signature, at a very slow tempo of about 55 BPM, evoking relaxation and calm. | `b6681025` | 1512 |
| `s07` | ko | 새벽 드라이브에 어울리는 재즈 피아노와 색소폰, A단조, 느리게 | `f3c6e2cc` | 2047 |
| `s08` | ko | 어두운 영화 장면용 패드와 베이스, F 프리지안 | `3d5a1000` | 1194 |
| `e03` | en | Late-night lo-fi study beats on electric piano, relaxed, 78 BPM | `6e77576a` | 1922 |
| `e06` | en | Bossa nova tune with nylon guitar and flute in A minor at 120 BPM | `30f41408` | 2163 |
| `e07` | en | Fast electronic dance track with synth lead, synth bass and drums, 128 BPM, 4/4, C minor. | `a1d8b6d3` | 2061 |
| `e09` | en | Blues shuffle with organ and electric guitar in E major at 92 BPM | `0fb13a64` | 2047 |
