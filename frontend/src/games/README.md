# 게임 연결 약속

게임을 앱 화면 구조(`src/training`, 화면 경로는 `src/app`)에 붙이는 방법이다. 앱이 측정 블록으로 기준값을 구한 뒤 게임 화면을 띄우고, 게임은 라운드가 끝날 때 기록을 돌려준다. 서버로 보내기·재시도·단계 저장은 앱이 한다. 기록 형식은 [`docs/api.md`](../../../docs/api.md) 7.2를 따른다.

## 흐름

1. 오늘 훈련 시작 → (첫 회차면 캘리브레이션) 측정 블록
2. 게임 선택: 영역별(호흡 / 말속도 / 억양)로 `registry.ts`의 게임을 보여 준다. 등록된 게임이 없는 영역은 "준비 중"
3. 게임 화면(라운드 하나) → `onRoundEnd` → 라운드 끝 화면(다음 라운드 / 다른 게임 / 오늘 훈련 끝)
4. 오늘 훈련 끝 → complete → 결과(서버 지표)

게임 화면은 라운드마다 새로 띄운다. 라운드 하나 안에서 여러 문제를 내는 것은 게임이 정한다(예: 말속도 게임은 문장 5개가 라운드 하나).

## 등록

`registry.ts`의 `GAMES`에 한 줄을 넣는다.

```ts
import { GuideReadingGame, MAX_LEVEL as GUIDE_READING_MAX_LEVEL } from './guide_reading';

export const GAMES: readonly GameEntry[] = [
  { game: 'guide_reading', area: 'rate', title: '노래방 따라 읽기', description: '파란 안내를 따라 같은 빠르기로 읽어요', maxLevel: GUIDE_READING_MAX_LEVEL, component: GuideReadingGame },
];
```

등록된 게임: `guide_reading`(말속도, `guide_reading/README.md`)

| 필드 | 뜻 |
|---|---|
| `game` | 게임 이름. `balloon`, `candle`, `spaceship`, `roller_coaster`, `guide_reading` 중 하나(api.md 7.2의 `game`) |
| `area` | `breath`(호흡), `rate`(말속도), `intonation`(억양) |
| `maxLevel` | 가장 높은 단계. 앱이 단계를 1~maxLevel 안에서 저장한다 |
| `component` | 게임 화면. 아래 `GameProps`를 받는다 |

영역 제안: balloon·candle → 호흡, guide_reading → 말속도, spaceship·roller_coaster → 억양

## 받는 값 (`GameProps`)

```ts
interface GameProps {
  baselines: {
    noiseFloorDbfs: number | null; //      잡음 바닥(dBFS)
    sustainedLevelDbfs: number | null; //  "아" 음량(dBFS)
    f0MedianHz: number | null; //          기본주파수 중앙값(Hz)
    f0SearchRangeHz: { min: number; max: number }; // 기본주파수 탐색 범위(Hz). 캘리브레이션이 없으면 75~500
    maxPhonationTimeS: number | null; //   최대발성지속시간(초)
    articulationRateSps: number | null; // 조음속도(음절/초, s1 낭독)
  };
  level: number; //       현재 단계(1부터). 앱이 게임별로 저장해 둔 값
  roundIndex: number; //  이번 회차에서 이 게임의 몇 번째 라운드인지(1부터)
  userKey: string; //     게임이 자기 상태를 사용자별로 저장할 때 쓰는 키(개발용 바로가기는 'dev')
  onRoundEnd: (run: GameRun, recording?: { fileUri: string }) => void;
  onExit: () => void;
}
```

- 기준값은 측정 블록에서 기기가 계산한 값이다. 구하지 못한 값은 `null`이니, 그때는 게임의 기본값을 쓴다.
- 음량은 dBFS 상대값이다(실제 소리 크기 dB SPL이 아님). 발성 기준은 `잡음 바닥 + 10 dB`.

## 돌려주는 값

- **라운드가 끝날 때** `onRoundEnd(run)`을 한 번 부른다. `run`은 api.md 7.2 형식 그대로다.

| 필드 | 넣는 값 |
|---|---|
| `game_run_id` | `uuidv4()`(`src/session`). 라운드마다 새로 |
| `game` | 등록한 게임 이름 |
| `level` | `props.level` |
| `round_index` | `props.roundIndex` |
| `started_at` | 라운드 시작 시각 `new Date().toISOString()`(시간대가 있어야 함) |
| `success_rate_pct` | 라운드 성공률 0~100(%). 0~1 비율이 아니다 |
| `decision` | 다음 단계 판단 `up` / `down` / `hold` / `deferred`. 앱이 이 값으로 다음 단계를 저장한다 |
| `decision_reason` | 판단 이유(선택, 500자 이하) |
| `params`, `result` | 게임별 설정·결과(JSON 객체, 선택). 요청 전체가 1 MB를 넘지 않게 |

- **나가기**: 라운드를 마치지 않고 나가면 `onExit()`. 그 라운드는 기록하지 않는다.
- **라운드 녹음(선택)**: 녹음 파일을 같이 올리려면 `recording`에 파일 URI를 넣는다. 녹음은 반드시 `WAV_FILE_OPTIONS`(`src/measurement`, 모노·16비트·48 kHz WAV)로 한다. 앱이 `kind = game`, 그 라운드 ID로 올린다.

## 마이크

- 마이크는 게임 화면이 뜬 뒤에 연다. 앱은 측정 블록이 끝난 뒤에만 게임 화면을 띄운다(녹음기 두 개를 동시에 쓰지 않기 위해).
- 게임 화면이 사라질 때(언마운트) 녹음을 멈춘다.
- 실시간 음량은 `VoiceBandFilter`(`src/measurement`)를 거친 뒤 `bufferLevelDbfs`로 잰다. 측정 블록 기준값과 같은 대역(60 Hz~8 kHz)이라 바로 비교할 수 있다.

```ts
import { bufferLevelDbfs, VoiceBandFilter } from '../../measurement';

const filter = new VoiceBandFilter(buffer.sampleRate); // 녹음을 시작할 때 하나
const level = bufferLevelDbfs(filter.process(buffer.getChannelData(0)));
const voiced = baselines.noiseFloorDbfs !== null && level > baselines.noiseFloorDbfs + 10;
```

## 지금 있는 게임 브랜치에 붙일 때

- 풍선·촛불(`feature_balloon-game`): `TempCalibration`의 `baselineDb` 대신 `baselines.sustainedLevelDbfs`. 라운드 끝에 `onRoundEnd(run)` 추가
- 우주선(`feat/spaceship-game`): `SpaceshipGame`은 그대로 두고 감싸는 화면만 만든다.
  - `onFinish` → `onRoundEnd`, `onExit` → `onExit`. `SpaceshipGameRun`은 `GameRun`과 필드가 같아 그대로 넘긴다
  - `SpaceshipBaseline`은 숫자만 받으니 `null`이면 150 Hz, −60 dBFS
  - `SpaceshipDemo`·`ResultView`의 반복은 앱의 라운드 끝 화면이 대신하므로 붙이지 않는다

```tsx
export function SpaceshipRound({ baselines, level, roundIndex, onRoundEnd, onExit }: GameProps) {
  const baseline = {
    f0MedianHz: baselines.f0MedianHz ?? 150,
    noiseFloorDbfs: baselines.noiseFloorDbfs ?? -60,
    f0RangeHz: baselines.f0SearchRangeHz,
  };
  return <SpaceshipGame baseline={baseline} level={level} roundIndex={roundIndex} onFinish={(run) => onRoundEnd(run)} onExit={onExit} />;
}
```

## 개발용 바로가기

`frontend/.env`의 `EXPO_PUBLIC_SCREEN`으로 화면을 바로 연다(`.env.example`에 빈 줄이 있다). 바꾼 뒤에는 Metro를 다시 시작한다. 개발 빌드(`__DEV__`)에서만 듣는다. 릴리스 APK는 값이 남아 있어도 앱 기본 화면을 연다.

| 값 | 여는 화면 |
|---|---|
| (비움) | 앱 기본 화면 |
| `measurement-test` | 측정 블록 시험(반복 측정 세션으로 서버까지 보냄) |
| `game-select` | 임시 기준값으로 게임 선택부터. 서버로 보내지 않음 |
| `game:<게임 이름>` | 임시 기준값으로 그 게임부터. 서버로 보내지 않음. 예: `game:guide_reading` |

임시 기준값: 잡음 바닥 −60 dBFS, "아" 음량 −25 dBFS, 기본주파수 중앙값 150 Hz, 탐색 범위 75~500 Hz, 최대발성지속시간 10초, 조음속도 4.0음절/초. 바로가기로 한 라운드 기록은 결과 화면과 Metro 로그(`[app] dev_runs`)에서 본다.
