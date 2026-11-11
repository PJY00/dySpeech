// [임시 연결 지점] 라운드 중 마이크 음량을 받는 곳. 게임 로직은 이 파일의 인터페이스(RoundInput)만 안다.
//
// ── 측정 블록(#20~#24)이 main에 들어오면 교체하는 방법 ──────────────────────────────
// 1. 이 파일의 useRoundInput을 MeasurementRecorder로 구현한다. 모양(RoundInput)은 그대로 둔다.
//      prepareMicrophone() 결과로 status 결정: 'loading' → 'ready' 또는 'denied'(실패하면 'error')
//      start(): new MeasurementRecorder().start(uuidv4(), (levelDbfs, dtS) => onLevel(levelDbfs, dtS))
//        - levelDbfs는 말소리 대역 필터를 거친 값이라 frameToDb를 쓰지 않는다
//      stop(): const left = await recorder.release(); if (left) await discardRecordingFile(left.fileUri)
//        - 호흡 게임은 녹음을 서버에 올리지 않으므로 만든 WAV는 버린다
//      VOICED_MARGIN_DB는 measurement/analysis의 VOICE_MARGIN_DB(10dB)를 쓴다
// 2. gameProps.ts는 지우지 않는다. 앱의 GameProps와 모양이 같고, gameTypes.ts의 위치가 바뀌어도(src/app → src/training) 영향이 없다.
//      gameProps.ts의 GameRun import만 '../../session/types'로 바꾼다
// 3. balloonLogic.ts의 median import를 '../../measurement/analysis'로 바꾼다
// 4. 아래 임시 파일을 지우고 balloonRun.ts의 import를 '../../session'(uuidv4, GameRun)으로 바꾼다
//      shared/audio/{audio,baseline,noiseFloor,tempUseMic,TempCalibration}, shared/api/gameRun.ts
// 5. App.tsx는 main 원본으로 되돌리고 games/registry.ts에 balloon, candle을 한 줄씩 추가한다
//      { game: 'balloon', area: 'breath', title: '풍선 불기', maxLevel: MAX_LEVEL, component: BalloonGameScreen }
// ───────────────────────────────────────────────────────────────────────────────────────
import { frameToDb } from '../../shared/audio/audio';
import { VOICING_MARGIN_DB } from '../../shared/audio/noiseFloor';
import { useMic } from '../../shared/audio/tempUseMic';

export const VOICED_MARGIN_DB = VOICING_MARGIN_DB;

export type RoundInputStatus = 'loading' | 'ready' | 'denied' | 'error';

export interface RoundInput {
  status: RoundInputStatus;
  error: string | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
}

/**
 * @param onLevel 프레임마다 호출: 음량(dBFS), 프레임 길이(초)
 */
export function useRoundInput(onLevel: (levelDbfs: number, dtS: number) => void): RoundInput {
  const mic = useMic(({ samples, sampleRate }) => onLevel(frameToDb(samples), samples.length / sampleRate));
  return {
    status: mic.error ? 'error' : 'ready',
    error: mic.error,
    start: mic.start,
    stop: mic.stop,
  };
}
