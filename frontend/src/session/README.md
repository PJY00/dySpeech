# 세션 모듈과 측정 블록

앱이 서버와 주고받는 흐름(세션 등록 → 녹음 업로드 → complete → 상태 확인)과 측정 블록 화면을 맡는다. 게임은 라운드 기록(원하면 녹음)만 넘기면 된다. 서버 API는 [`docs/api.md`](../../../docs/api.md), 지표 정의는 [`docs/metrics-spec.md`](../../../docs/metrics-spec.md)를 따른다.

- `src/session/`: 세션·녹음·게임 라운드 저장, 서버로 보내기, 재시도, 이어서 보내기
- `src/measurement/`: 측정 블록 화면, 녹음기 설정, 기기 지표 계산

## 설정

`frontend/.env`에 넣는다(`.env.example` 참고).

| 변수 | 뜻 |
|---|---|
| `EXPO_PUBLIC_API_URL` | 서버 주소. 예: `http://192.168.0.10:8000`(로컬 서버는 폰에서 닿는 IP로) |
| `EXPO_PUBLIC_APP_KEY` | 앱 키(`X-App-Key`). 앱 파일에서 꺼낼 수 있으므로 진짜 비밀은 아니다(api.md 2.1) |

둘 중 하나가 비어 있으면 세션은 기기에 저장만 하고 보내지 않는다(상태 `queued`, 오류 `CONFIG_MISSING`).

## 사용법

```tsx
import { getLatestBaselines, resumePending, startAutoSync, startSession } from '../session';
import type { Baselines, GameRun } from '../session';
import { MeasurementBlock } from '../measurement';

// 앱 시작 때 한 번: 못 보낸 세션 이어서 보내기 + 앞으로 올 때마다 다시 보내기
const { unfinished } = await resumePending(); // complete 전에 멈춘 세션: complete() 또는 discard()
const stopAutoSync = startAutoSync();

// 1) 세션 시작: 'training' | 'retest' | 'agc_test'
const session = await startSession('training');

// 2) 측정 블록. 첫 회차(session.needsCalibration)면 캘리브레이션이 자동으로 앞에 붙는다
<MeasurementBlock session={session} onDone={(b: Baselines) => startGame(b)} onCancel={() => session.discard()} />;

// 3) 기준값(구하지 못하면 null). 측정 블록을 건너뛴 경우는 getLatestBaselines()
b.noiseFloorDbfs; //      잡음 바닥(dBFS)
b.sustainedLevelDbfs; //  "아" 음량(dBFS)
b.f0MedianHz; //          기본주파수 중앙값(Hz)
b.f0SearchRangeHz; //     기본주파수 탐색 범위(Hz, 서버와 같은 규칙)
b.maxPhonationTimeS; //   최대발성지속시간(초)

// 4) 라운드가 끝날 때마다(api.md 7.2 형식 그대로, success_rate_pct는 0~100)
await session.addGameRun(run);
// 게임 녹음은 선택: WAV_FILE_OPTIONS로 녹음한 파일을 그 라운드 ID로
await session.addRecording({ kind: 'game', gameRunId: run.game_run_id, fileUri });

// 5) 끝
await session.complete(); // 끝난 시각을 고정해 저장하고 보내기 시작. 오프라인이어도 바로 돌아온다
const p = await session.waitForResult({ onProgress: (x) => console.log(x.state) });
// p.state: 'done' | 'failed' | 'processing'(3분 넘게 처리 중) | 'queued'(못 보냄, 다음에 다시) | 'blocked'
// p.server: GET /v1/sessions/{id} 응답(서버 지표, failed면 error.code)
```

- 게임 라운드 ID 등 ID는 `uuidv4()`(이 모듈)로 만든다. 새 네이티브 패키지 없이 JS로 만든다.
- 게임은 측정 블록의 `onDone` 뒤에 마이크를 연다(녹음기 두 개를 동시에 쓰지 않기 위해).
- 음량은 dBFS 상대값이다. 실제 소리 크기(dB SPL)가 아니다.
- 개발용: `startNewDevUser()`로 새 사용자를 만들면 그 사용자의 세션은 `client_meta.dev = true`로 보내고, ID는 `devUserIds()`에 남는다(시험 데이터 정리 대상).

## 상태와 재시도

| 기기 상태 | 뜻 |
|---|---|
| `recording` | complete 전(녹음·게임 중) |
| `queued` | 보내기 대기(처음이거나, 다시 보내도 안 되어 다음 계기를 기다림) |
| `sending` | 보내는 중 |
| `processing` | 서버가 complete를 받음(202). 서버 계산 중 |
| `done` / `failed` | 서버 결과 |
| `blocked` | 다시 보내도 같은 오류. `lastError`를 보고 앱을 고친다 |

| 상황 | 처리 |
|---|---|
| 네트워크 끊김, 500 | 2·5·15·30·60초 간격으로 다시 보냄(최대 5회). 그래도 안 되면 `queued`로 두고 다음에 앱을 켜거나 앞으로 가져올 때 다시 |
| 404 `USER_NOT_FOUND` / `SESSION_NOT_FOUND` | 사용자나 세션 등록부터 다시 |
| S3 PUT 403(URL 만료) | 같은 녹음 ID로 업로드 URL을 다시 받음 |
| 409 `MISSING_RECORDINGS` | 빠진 녹음만 다시 올리고 complete를 다시 보냄 |
| 401, 409 `ID_CONFLICT`, 413, 422 | `blocked`(422는 `fields`·`invalid_recordings`를 `lastError.message`에 남김) |
| 202 뒤 | 4초 간격으로 상태 확인, 3분이 지나면 멈추고 다음에 다시 확인 |

- 같은 ID는 같은 결과를 내므로(api.md 2.6) 끊기면 처음부터 다시 보낸다. ID는 만드는 즉시 저장한다.
- 202를 받으면 기기의 WAV 복사 파일을 지운다.
- 저장 위치: 문서 폴더의 `dyspeech/state/`(세션·사용자 문서), `dyspeech/recordings/`(WAV).

## 녹음 규격

`src/measurement/recorder.ts`의 `WAV_FILE_OPTIONS`(react-native-audio-api 0.13.6): 모노, 16비트 PCM, 48 kHz WAV. 라이브러리 기본값은 스테레오·M4A·24비트이므로 이 설정을 꼭 쓴다. 앱은 녹음을 자르거나 음량 정규화·잡음 제거를 하지 않는다. `addRecording`이 WAV 헤더를 서버와 같은 기준으로 검사하고, 다르면 `RecordingRejected`를 낸다.

## 측정 블록

시간 값(녹음 길이, 자동 멈춤, 최소 발성 1초, 시끄러움 기준 −40 dBFS)은 모두 초기값이다. 바꾸는 곳은 `src/measurement/steps.ts`.

| 순서 | 녹음 | 끝내는 방법 |
|---|---|---|
| 첫 회차만 | `calibration_glide`: 낮은 소리에서 높은 소리로 "아" | 소리 뒤 1.5초 조용하면 자동, 최대 10초 |
| 1 | `noise_floor`: 조용히 | 2.5초 뒤 자동 |
| 2 | `mpt_trial` 3회(3회째는 건너뛸 수 있음) | "그만" 또는 소리 뒤 1.5초 조용하면 자동, 최대 60초 |
| 3 | `eval_sentence`(s1) | "다 읽었어요" 또는 소리 뒤 2초 조용하면 자동, 최대 30초 |

- 녹음마다 검사한다. 소리가 없거나 짧으면 "다시 하기"(또는 "이대로 쓰기"), WAV 형식이 다르면 다시 하기만 된다.
- 기기 지표 4개(`noise_floor`, `max_phonation_time`, `sustained_level`, `f0_median`)를 서버와 같은 정의로 계산해 `device_measurements`로 보낸다. 공식 값은 서버 값이다.
- 음량과 기본주파수는 서버와 같은 대역에서 잰다: 60 Hz 고역 통과 + 8 kHz 저역 통과(각 4차 버터워스, 녹음 파일은 앞뒤로 두 번). 그래서 기기 잡음 바닥이 서버 값과 맞고, 게임의 발성 기준(잡음 바닥 + 10 dB)도 서버와 같은 기준이 된다.
- 게임이 실시간 음량을 잴 때도 `VoiceBandFilter`(`src/measurement`)를 거친 뒤 재야 측정 블록 기준값과 비교할 수 있다.
- 헤드셋이 유선(3.5 mm 또는 USB-C)이 아니면 경고만 띄운다.
- 개발용 시험 화면: `MeasurementTestScreen`(반복 측정 세션으로 측정 블록 → complete → 서버 결과).

## 테스트

```
npm test
npm run typecheck
```

- node:test로 한다. 패키지를 더하지 않으려고 vitest 대신 Node 24에 들어 있는 테스트 실행기와 TypeScript 타입 제거를 쓴다.
- `src/session/testing/resolve-ts.mjs`가 확장자 없는 import를 `.ts`로 찾게 한다. 그래서 시험하는 모듈은 enum을 쓰지 않고, 타입은 `import type`으로 가져오고, React Native·Expo를 import하지 않는 순수 모듈로 둔다(화면·녹음기·저장소 어댑터는 폰에서 확인).
- 타입 검사는 둘로 나눈다(`npm run typecheck`가 둘 다 한다). 앱 코드는 `tsconfig.json`(Node 타입 없음, 테스트·`testing` 폴더 제외)으로 검사해서 폰에 없는 Node 전역(`Buffer` 등)을 쓰면 잡는다. 테스트와 `testing` 폴더는 `tsconfig.test.json`(`types: ["node"]`, `@types/node` 24.19.2)으로 검사한다.
- 정답을 아는 합성 신호(`src/session/testing/synth.ts`)와 가짜 서버(`testing/fakeServer.ts`)로 시험한다.
- 로컬 백엔드 통합 시험은 환경 변수가 있을 때만 돈다(`src/session/__tests__/server.it.test.ts` 맨 위 설명). 로컬 테스트 DB와 실제 S3로 띄운 서버에 합성 WAV로 반복 측정 한 회차를 보내 `done`까지 확인한다. 만든 사용자 ID는 정리 목록 파일에 적고, `backend/tools/cleanup.py`로 지운다.
