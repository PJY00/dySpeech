// UI: 마이크(useMic) → 음향 처리(audio) → 판정(balloonLogic)을 연결하고 풍선을 그린다.
// 기준 음량(baselineDb)은 앱 시작 시 측정된 값을 prop으로 받는다.
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { frameToDb } from '../../shared/audio/audio';
import {
  BalloonState,
  initialBalloonState,
  stepBalloon,
  TARGET_DELTA_DB,
  TARGET_HOLD_SECONDS,
} from './balloonLogic';
import { MicFrame, useMic } from '../../shared/audio/tempUseMic';

type Phase = 'ready' | 'playing' | 'passed';

const BALLOON_MIN_SCALE = 0.3;
const BALLOON_MAX_SCALE = 1.6;
const BALLOON_BASE_SIZE = 160;

type Props = {
  /** 기준 음량 (dBFS). 앱 시작 시 측정한 값 */
  baselineDb: number;
};

export default function BalloonGameScreen({ baselineDb }: Props) {
  const [phase, setPhase] = useState<Phase>('ready');
  const [balloon, setBalloon] = useState<BalloonState>(initialBalloonState);
  const [dbDiff, setDbDiff] = useState(0);

  // 프레임 콜백에서 쓰는 값은 ref로 들고 있어야 최신 값이 보장된다.
  const phaseRef = useRef<Phase>('ready');
  const baselineRef = useRef(baselineDb);
  baselineRef.current = baselineDb;
  const balloonRef = useRef<BalloonState>(initialBalloonState);

  const changePhase = (next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  };

  const handleFrame = useCallback(({ samples, sampleRate }: MicFrame) => {
    if (phaseRef.current !== 'playing') return;

    const dt = samples.length / sampleRate;
    const diff = frameToDb(samples) - baselineRef.current;

    balloonRef.current = stepBalloon(balloonRef.current, diff, dt);
    setDbDiff(diff);
    setBalloon(balloonRef.current);

    if (balloonRef.current.passed) {
      changePhase('passed');
      void stop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { start, stop, error } = useMic(handleFrame);

  const startGame = async () => {
    balloonRef.current = initialBalloonState;
    setBalloon(initialBalloonState);
    setDbDiff(0);
    changePhase('playing');
    await start();
  };

  const reset = async () => {
    await stop();
    changePhase('ready');
    balloonRef.current = initialBalloonState;
    setBalloon(initialBalloonState);
  };

  const scale =
    BALLOON_MIN_SCALE + (BALLOON_MAX_SCALE - BALLOON_MIN_SCALE) * balloon.size;

  return (
    <View style={styles.container}>
      <Text style={styles.title}>풍선 불기</Text>

      <Text style={styles.guide}>
        {phase === 'ready' &&
          `"아~" 소리를 기준보다 크게(+${TARGET_DELTA_DB}dB 이상) ${TARGET_HOLD_SECONDS}초 동안 유지하세요.`}
        {phase === 'playing' &&
          `기준보다 크게(+${TARGET_DELTA_DB}dB 이상) ${TARGET_HOLD_SECONDS}초 동안 유지하세요.`}
        {phase === 'passed' && '통과했어요! 🎉'}
      </Text>

      <View style={styles.balloonArea}>
        <View
          style={[
            styles.balloon,
            phase === 'passed' && styles.balloonPassed,
            { transform: [{ scale }] },
          ]}
        />
      </View>

      <View>
        <Text style={styles.info}>
          기준 {baselineDb.toFixed(1)} dB
          {phase !== 'ready' &&
            ` / 현재 차이 ${dbDiff >= 0 ? '+' : ''}${dbDiff.toFixed(1)} dB`}
        </Text>
        {phase !== 'ready' && (
          <Text style={styles.info}>
            유지 {balloon.holdTime.toFixed(1)} / {TARGET_HOLD_SECONDS}초
          </Text>
        )}
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      <View style={styles.buttons}>
        {phase === 'ready' && <Button label="시작" onPress={startGame} />}
        {phase !== 'ready' && <Button label="다시 하기" onPress={reset} />}
      </View>
    </View>
  );
}

function Button({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.button} onPress={onPress}>
      <Text style={styles.buttonText}>{label}</Text>
    </Pressable>
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
  balloonArea: {
    width: BALLOON_BASE_SIZE * BALLOON_MAX_SCALE,
    height: BALLOON_BASE_SIZE * BALLOON_MAX_SCALE,
    alignItems: 'center',
    justifyContent: 'center',
  },
  balloon: {
    width: BALLOON_BASE_SIZE,
    height: BALLOON_BASE_SIZE,
    borderRadius: BALLOON_BASE_SIZE / 2,
    backgroundColor: '#ef5350',
  },
  balloonPassed: { backgroundColor: '#66bb6a' },
  info: { fontSize: 15, textAlign: 'center', color: '#555' },
  error: { fontSize: 15, color: '#d32f2f' },
  buttons: { flexDirection: 'row', gap: 12 },
  button: {
    backgroundColor: '#1976d2',
    paddingHorizontal: 28,
    paddingVertical: 12,
    borderRadius: 8,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
