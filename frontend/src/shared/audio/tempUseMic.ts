// [임시] 입력: 마이크를 열고, 프레임이 들어올 때마다 콜백으로 넘긴다.
// 음성 입력 부분은 팀원별로 따로 만든 뒤 합치기로 했으므로, 합칠 때 이 파일을 대체한다.
// 계약: useMic(onFrame) → { start, stop, running, error }, onFrame({ samples, sampleRate })
import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioManager, AudioRecorder } from 'react-native-audio-api';

export type MicFrame = {
  /** -1.0 ~ 1.0 PCM (모노) */
  samples: Float32Array;
  sampleRate: number;
};

const SAMPLE_RATE = 44100;
const BUFFER_LENGTH = 2048; // 약 46ms

export function useMic(onFrame: (frame: MicFrame) => void) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<AudioRecorder | null>(null);
  // 콜백이 바뀌어도 녹음을 다시 시작하지 않도록 ref에 보관
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;

  const stop = useCallback(async () => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder) {
      recorder.clearOnAudioReady();
      await recorder.stop();
    }
    setRunning(false);
  }, []);

  const start = useCallback(async () => {
    if (recorderRef.current) return;
    setError(null);

    const permission = await AudioManager.requestRecordingPermissions();
    if (permission !== 'Granted') {
      setError('마이크 권한이 필요합니다.');
      return;
    }

    const recorder = new AudioRecorder();
    recorder.onAudioReady(
      { sampleRate: SAMPLE_RATE, bufferLength: BUFFER_LENGTH, channelCount: 1 },
      ({ buffer }) => {
        onFrameRef.current({
          samples: buffer.getChannelData(0),
          sampleRate: buffer.sampleRate,
        });
      },
    );

    const result = await recorder.start();
    if (result.status === 'error') {
      setError('마이크를 시작하지 못했습니다.');
      return;
    }
    recorderRef.current = recorder;
    setRunning(true);
  }, []);

  // 화면에서 나가면 마이크 정리
  useEffect(() => {
    return () => {
      void stop();
    };
  }, [stop]);

  return { start, stop, running, error };
}
