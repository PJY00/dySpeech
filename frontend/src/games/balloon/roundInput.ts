// 라운드 중 마이크 음량을 받는 곳. 앱 측정 블록의 녹음기(MeasurementRecorder)를 쓴다.
// 게임 로직(useBalloonRound)은 이 파일의 인터페이스(RoundInput)만 안다.
//
// - 음량은 말소리 대역(60 Hz~8 kHz) 필터를 거친 dBFS라서 측정 블록 기준값과 바로 비교할 수 있다.
// - 녹음기는 항상 WAV 파일을 만든다. 호흡 게임은 녹음을 서버에 올리지 않으므로 멈출 때 파일을 지운다.
// - 마이크는 게임 화면이 뜬 뒤에 열고, 화면이 사라지면 멈춘다(녹음기 두 개를 동시에 쓰지 않기 위해).
import { useCallback, useEffect, useRef, useState } from 'react';

import { VOICE_MARGIN_DB } from '../../measurement/analysis';
import { MeasurementRecorder, prepareMicrophone } from '../../measurement/recorder';
import { discardRecordingFile } from '../../session';
import { uuidv4 } from '../../session/uuid';

/** 잡음 바닥보다 이만큼(dB) 커야 소리가 난 것으로 본다 (서버·측정 블록과 같은 기준) */
export const VOICED_MARGIN_DB = VOICE_MARGIN_DB;

/** loading: 마이크 준비 중 / ready: 쓸 수 있음 / denied: 권한 거부 / error: 오류 */
export type RoundInputStatus = 'loading' | 'ready' | 'denied' | 'error';

export interface RoundInput {
  status: RoundInputStatus;
  error: string | null;
  /** 마이크를 열고 onLevel을 부르기 시작한다 */
  start: () => Promise<void>;
  /** 마이크를 멈추고 만든 파일을 지운다(여러 번 불러도 된다) */
  stop: () => Promise<void>;
}

const messageOf = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * @param onLevel 프레임마다 호출(약 20ms): 음량(dBFS, 필터 적용), 프레임 길이(초)
 */
export function useRoundInput(onLevel: (levelDbfs: number, dtS: number) => void): RoundInput {
  const [status, setStatus] = useState<RoundInputStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MeasurementRecorder | null>(null);
  // 콜백이 바뀌어도 녹음을 다시 시작하지 않도록 ref에 보관
  const onLevelRef = useRef(onLevel);
  onLevelRef.current = onLevel;

  const stop = useCallback(async () => {
    const left = await recorderRef.current?.release();
    if (left) await discardRecordingFile(left.fileUri);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if ((await prepareMicrophone()) !== 'granted') {
          if (!cancelled) setStatus('denied');
          return;
        }
        recorderRef.current = new MeasurementRecorder();
        if (!cancelled) setStatus('ready');
      } catch (e) {
        if (!cancelled) {
          setError(messageOf(e));
          setStatus('error');
        }
      }
    })();
    return () => {
      cancelled = true;
      void stop();
    };
  }, [stop]);

  const start = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    try {
      await recorder.start(uuidv4(), (levelDbfs, dtS) => onLevelRef.current(levelDbfs, dtS));
    } catch (e) {
      setError(messageOf(e));
      setStatus('error');
    }
  }, []);

  return { status, error, start, stop };
}
