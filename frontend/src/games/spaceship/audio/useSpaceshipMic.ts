import { useEffect, useRef, useState } from 'react';

import { MeasurementRecorder, prepareMicrophone } from '../../../measurement/recorder';
import { discardRecordingFile } from '../../../session';
import { uuidv4 } from '../../../session/uuid';
import {
  decimate, PitchTracker, REALTIME_HOP_SEC, REALTIME_PITCH_RATE, type PitchFrame, type PitchTrackerOptions,
} from './PitchTracker';

export type MicStatus = 'starting' | 'running' | 'denied' | 'error';


export function useSpaceshipMic(
  onFrame: (frame: PitchFrame) => void,
  options: PitchTrackerOptions,
): { status: MicStatus; error: string | null; sampleRate: number | null } {
  const [status, setStatus] = useState<MicStatus>('starting');
  const [error, setError] = useState<string | null>(null);
  const [sampleRate, setSampleRate] = useState<number | null>(null);
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const optionsRef = useRef(options);

  useEffect(() => {
    let cancelled = false;
    const recorder = new MeasurementRecorder();
    let tracker: PitchTracker | null = null;
    let inputRate = 0;
    let factor = 1;

    void (async () => {
      try {
        if ((await prepareMicrophone()) !== 'granted') {
          if (!cancelled) setStatus('denied');
          return;
        }
        if (cancelled) return;
        await recorder.start(uuidv4(), (levelDbfs, _dt, samples, rate) => {
          if (cancelled) return;
          if (!tracker || rate !== inputRate) {
            inputRate = rate;
            factor = Math.max(1, Math.round(rate / REALTIME_PITCH_RATE));
            tracker = new PitchTracker(rate / factor, { ...optionsRef.current, hopSec: REALTIME_HOP_SEC });
            setSampleRate(rate);
          }
          for (const f of tracker.push(decimate(samples, factor))) {
            onFrameRef.current({ ...f, levelDbfs });
          }
        });
        if (cancelled) {
          const left = await recorder.release();
          if (left) await discardRecordingFile(left.fileUri);
          return;
        }
        setStatus('running');
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setStatus('error');
        }
      }
    })();

    return () => {
      cancelled = true;
      void (async () => {
        const left = await recorder.release();
        if (left) await discardRecordingFile(left.fileUri);
      })();
    };
  }, []);

  return { status, error, sampleRate };
}
