// [임시] 기준선 측정 화면.
//  - noiseFloorDb: 조용히 있는 구간의 음량(하위 20% 백분위)
//  - baselineDb: "아~" 소리를 낸 구간의 음량 중앙값 (잡음 바닥 + 10dB 이상인 프레임만)
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { frameToDb, median } from './audio';
import type { Baseline } from './baseline';
import { estimateNoiseFloor, voicingThresholdDb } from './noiseFloor';
import { MicFrame, useMic } from './tempUseMic';

/** 조용히 있는 시간(초) */
const QUIET_SECONDS = 2;
/** 기준 음량을 만들기 위해 모아야 하는 발성 시간(초) */
const VOICED_SECONDS = 2;

type Step = 'idle' | 'quiet' | 'voice';

export default function TempCalibration({
  onDone,
}: {
  onDone: (baseline: Baseline) => void;
}) {
  const [step, setStep] = useState<Step>('idle');
  const [progress, setProgress] = useState(0);

  const stepRef = useRef<Step>('idle');
  const quietDbRef = useRef<number[]>([]);
  const quietSecRef = useRef(0);
  const noiseFloorRef = useRef(0);
  const voicedDbRef = useRef<number[]>([]);
  const voicedSecRef = useRef(0);

  const changeStep = (next: Step) => {
    stepRef.current = next;
    setStep(next);
    setProgress(0);
  };

  const handleFrame = useCallback(({ samples, sampleRate }: MicFrame) => {
    const db = frameToDb(samples);
    const dt = samples.length / sampleRate;

    if (stepRef.current === 'quiet') {
      quietDbRef.current.push(db);
      quietSecRef.current += dt;
      setProgress(Math.min(1, quietSecRef.current / QUIET_SECONDS));
      if (quietSecRef.current >= QUIET_SECONDS) {
        noiseFloorRef.current = estimateNoiseFloor(quietDbRef.current);
        changeStep('voice');
      }
      return;
    }

    if (stepRef.current === 'voice') {
      if (db < voicingThresholdDb(noiseFloorRef.current)) return; // 무음은 제외
      voicedDbRef.current.push(db);
      voicedSecRef.current += dt;
      setProgress(Math.min(1, voicedSecRef.current / VOICED_SECONDS));
      if (voicedSecRef.current >= VOICED_SECONDS) {
        stepRef.current = 'idle';
        void stop();
        onDone({
          baselineDb: median(voicedDbRef.current),
          noiseFloorDb: noiseFloorRef.current,
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onDone]);

  const { start, stop, error } = useMic(handleFrame);

  const begin = async () => {
    quietDbRef.current = [];
    quietSecRef.current = 0;
    voicedDbRef.current = [];
    voicedSecRef.current = 0;
    changeStep('quiet');
    await start();
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>기준 음량 측정</Text>
      <Text style={styles.guide}>
        {step === 'idle' &&
          '먼저 2초 동안 조용히 있고, 이어서 편한 크기로 "아~" 소리를 내서 내 기준 음량을 측정해요.'}
        {step === 'quiet' && '조용히 계세요…'}
        {step === 'voice' && '"아~" 소리를 일정한 크기로 계속 내 주세요.'}
      </Text>
      {step !== 'idle' && (
        <Text style={styles.info}>
          {step === 'quiet' ? '조용히 측정 중' : '소리 측정 중'}{' '}
          {Math.round(progress * 100)}%
        </Text>
      )}
      {error && <Text style={styles.error}>{error}</Text>}
      {step === 'idle' && (
        <Pressable style={styles.button} onPress={begin}>
          <Text style={styles.buttonText}>측정 시작</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 16,
  },
  title: { fontSize: 28, fontWeight: '700' },
  guide: { fontSize: 16, textAlign: 'center', color: '#333' },
  info: { fontSize: 15, color: '#555' },
  error: { fontSize: 15, color: '#d32f2f' },
  button: {
    backgroundColor: '#1976d2',
    paddingHorizontal: 28,
    paddingVertical: 12,
    borderRadius: 8,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
