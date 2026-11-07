// [임시] 기준 음량 측정 화면.
// 실제 앱에서는 앱 시작 시 한 번 측정해서 모든 게임이 공유한다. 그때 이 파일을 대체한다.
// 계약: 측정이 끝나면 onDone(baselineDb)를 호출한다. (baselineDb = 기준 음량, dBFS 중앙값)
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { frameToDb, median } from './audio';
import { MicFrame, useMic } from './tempUseMic';

/** 이 값보다 작은 프레임은 무음으로 보고 제외 (dBFS) */
const NOISE_GATE_DB = -50;
/** 기준선을 만들기 위해 모아야 하는 발성 시간(초) */
const VOICED_SECONDS = 2;

export default function TempCalibration({
  onDone,
}: {
  onDone: (baselineDb: number) => void;
}) {
  const [measuring, setMeasuring] = useState(false);
  const [progress, setProgress] = useState(0);

  const doneRef = useRef(false);
  const voicedDbRef = useRef<number[]>([]);
  const voicedSecondsRef = useRef(0);

  const handleFrame = useCallback(({ samples, sampleRate }: MicFrame) => {
    if (doneRef.current) return;
    const db = frameToDb(samples);
    if (db < NOISE_GATE_DB) return;

    voicedDbRef.current.push(db);
    voicedSecondsRef.current += samples.length / sampleRate;
    setProgress(Math.min(1, voicedSecondsRef.current / VOICED_SECONDS));

    if (voicedSecondsRef.current >= VOICED_SECONDS) {
      doneRef.current = true;
      void stop();
      onDone(median(voicedDbRef.current));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onDone]);

  const { start, stop, error } = useMic(handleFrame);

  const begin = async () => {
    doneRef.current = false;
    voicedDbRef.current = [];
    voicedSecondsRef.current = 0;
    setProgress(0);
    setMeasuring(true);
    await start();
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>기준 음량 측정</Text>
      <Text style={styles.guide}>
        {measuring
          ? '"아~" 소리를 일정한 크기로 계속 내 주세요.'
          : '편한 크기로 "아~" 소리를 내서 내 기준 음량을 측정해요.'}
      </Text>
      {measuring && (
        <Text style={styles.info}>측정 중 {Math.round(progress * 100)}%</Text>
      )}
      {error && <Text style={styles.error}>{error}</Text>}
      {!measuring && (
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
