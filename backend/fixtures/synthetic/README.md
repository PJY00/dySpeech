# 합성 신호와 정답값

서버 지표 계산이 맞는지 확인하는 합성 녹음이다. 정답을 아는 신호로 계산 결과를 비교한다.

- `signals.json`: 만드는 값(길이, 음량, 기본주파수, 음절 길이, 휴지)
- `generate.py`: WAV와 정답값을 만든다. numpy와 scipy만 쓰고 서버 코드는 쓰지 않는다.
- `answers.json`: 정답값과 허용 범위(±). `signals.json`의 값에서 식으로 바로 구한다.

WAV는 저장소에 올리지 않는다(`.gitignore`). 필요할 때 만든다.

```bash
cd backend
python fixtures/synthetic/generate.py --out /tmp/synthetic   # WAV 만들기
python fixtures/synthetic/generate.py --answers              # signals.json을 바꾼 뒤 정답값 다시 쓰기
```

## 파일

모두 48 kHz 모노 16비트 PCM이다. 배경에는 −60 dBFS 잡음(100~7000 Hz)이 깔려 있다. 소리는 7000 Hz 아래만 써서, 서버가 16 kHz로 바꿔도 크기가 그대로다.

| 파일 | 녹음 종류 | 확인하는 것 |
|---|---|---|
| `noise_floor` | `noise_floor` | 잡음 바닥 = −60 dBFS |
| `level_steps` | (확인용) | 프레임 음량(dBFS): 6 dB 단계 4개 |
| `broken_phonation` | `mpt_trial` | 0.15초 끊김은 이어 붙이고 0.5초 끊김에서 나눈다. 0.05초 조각은 버린다. 최대발성지속시간 5.0초, 음량 감쇠 기울기 −2 dB/초 |
| `steady_phonation` | `mpt_trial` | 끊김 없는 3.0초. 기울기 0 |
| `f0_glide` | `calibration_glide` | 150 → 300 Hz(12반음) 글라이드. 편한 음높이 범위(5·95백분위), 반음 표준편차 12 ÷ √12, 음도 범위 12 × 0.9 |
| `syllable_train` | `eval_sentence` | 말소리 같은 음절 21개를 쉬지 않고 읽음. 휴지 0 |
| `paused_speech` | `eval_sentence` | 음절 8 + 7 + 6개, 휴지 0.6초·0.5초, 첫 구절 안의 0.15초 끊김은 휴지가 아님 |
| `paused_speech_louder` | `eval_sentence` | `paused_speech`를 3 dB 크게. 평균 음량 변화 +3 dB |

- 말소리 같은 음절: 성문 펄스(구절마다 내려가는 기본주파수) → 시간에 따라 바뀌는 포먼트 3개 → 자음처럼 짧은 고주파 잡음. 단순한 정현파는 발화 검출이 말소리로 보지 않아서 이렇게 만든다.
- 말속도 정답의 시간: 첫 음절의 자음 시작부터 마지막 모음 끝까지(T_total), 구절 사이 휴지의 합(T_pause)
- 억양 정답: 모음 구간에서 10 ms마다 잰 기본주파수로 구한다.
- 검출 음절 수(보조)는 "읽기 오류 가능" 표시(30% 이상 차이)에만 쓰므로, 허용 범위도 정답의 30%다.

## 한 회차(`session`)

`answers.json`의 `session`은 위 파일들로 한 회차를 보낼 때 서버 지표의 정답이다. 첫 회차(기준선)라서 평균 음량 변화는 0이다.
