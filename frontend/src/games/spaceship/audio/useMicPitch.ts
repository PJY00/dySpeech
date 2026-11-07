import { useEffect, useRef, useState } from 'react';
import { AudioManager, AudioRecorder } from 'react-native-audio-api';
import { PitchTracker, type PitchFrame, type PitchTrackerOptions } from './PitchTracker';

export type MicStatus = 'idle' | 'starting' | 'running' | 'denied' | 'error';

const REQUESTED_SAMPLE_RATE = 48000;
const CALLBACK_SECONDS = 0.02;

let sharedRecorder: AudioRecorder | null = null;
function getRecorder(): AudioRecorder {
  if (!sharedRecorder) sharedRecorder = new AudioRecorder();
  return sharedRecorder;
}

let queue: Promise<unknown> = Promise.resolve();
function enqueue<T>(job: () => Promise<T>): Promise<T> {
  const next = queue.then(job, job);
  queue = next.catch(() => undefined);
  return next;
}

export function useMicPitch(
  active: boolean,
  onFrame: (frame: PitchFrame) => void,
  trackerOptions?: PitchTrackerOptions,
): { status: MicStatus; error: string | null; sampleRate: number | null } {
  const [status, setStatus] = useState<MicStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [sampleRate, setSampleRate] = useState<number | null>(null);
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const optsRef = useRef(trackerOptions);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let tracker: PitchTracker | null = null;
    const recorder = getRecorder();
    setStatus('starting');
    setError(null);

    enqueue(async () => {
      if (cancelled) return;
      try {
        const perm = await AudioManager.requestRecordingPermissions();
        if (cancelled) return;
        if (perm !== 'Granted') {
          setStatus('denied');
          return;
        }
        AudioManager.setAudioSessionOptions({
          iosCategory: 'playAndRecord',
          iosMode: 'measurement',
          iosOptions: ['allowBluetoothHFP'],
        });
        await AudioManager.setAudioSessionActivity(true);
        if (cancelled) return;

        recorder.onAudioReady(
          {
            sampleRate: REQUESTED_SAMPLE_RATE,
            bufferLength: Math.round(REQUESTED_SAMPLE_RATE * CALLBACK_SECONDS),
            channelCount: 1,
          },
          ({ buffer }) => {
            if (cancelled) return;
            if (!tracker || tracker.sampleRate !== buffer.sampleRate) {
              tracker = new PitchTracker(buffer.sampleRate, optsRef.current);
              setSampleRate(buffer.sampleRate);
            }
            const frames = tracker.push(buffer.getChannelData(0));
            for (const f of frames) onFrameRef.current(f);
          },
        );
        const result = await recorder.start();
        if (result.status === 'error') throw new Error(result.message);
        if (!cancelled) setStatus('running');
      } catch (e) {
        if (!cancelled) {
          setStatus('error');
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    });

    return () => {
      cancelled = true;
      setStatus('idle');
      enqueue(async () => {
        try {
          recorder.clearOnAudioReady();
          if (recorder.isRecording()) await recorder.stop();
          await AudioManager.setAudioSessionActivity(false);
        } catch {
        }
      });
    };
  }, [active]);

  return { status, error, sampleRate };
}
