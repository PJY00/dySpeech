// UI: 풍선 게임과 같은 판정(balloonLogic)을 쓰고, 표시 객체만 촛불로 바꾼 화면.
// 목표 음량 이상을 유지할수록 불꽃 색이 실제 불꽃 온도 순서대로 변하고,
// 목표 시간만큼 유지해 목표 색(푸른 흰색)에 도달하면 통과.
// 기준 음량(baselineDb)은 앱 시작 시 측정된 값을 prop으로 받는다.
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { frameToDb } from '../../shared/audio/audio';
import { MicFrame, useMic } from '../../shared/audio/tempUseMic';
import {
  BalloonState,
  initialBalloonState,
  stepBalloon,
  TARGET_DELTA_DB,
  TARGET_HOLD_SECONDS,
} from './balloonLogic';
import { flameColor, flameCoreColor, flameKelvin } from './flameColor';

type Phase = 'ready' | 'playing' | 'passed';

// 불꽃 크기는 고정, 색만 변한다.
const FLAME_WIDTH = 70;
const FLAME_HEIGHT = 130;
/** 목표 음량 아래로 떨어졌을 때 색이 식는 속도 (heat 1.0 = 목표 색, 초당) */
const COOL_PER_SEC = 0.5;

type Props = {
  /** 기준 음량 (dBFS). 앱 시작 시 측정한 값 */
  baselineDb: number;
};

export default function CandleGameScreen({ baselineDb }: Props) {
  const [phase, setPhase] = useState<Phase>('ready');
  const [candle, setCandle] = useState<BalloonState>(initialBalloonState);
  const [dbDiff, setDbDiff] = useState(0);
  // 불꽃 색 진행도 0~1 (1 = 목표 색). 유지 시간에 따라 오르고, 소리가 작아지면 서서히 식는다.
  const [heat, setHeat] = useState(0);

  // 프레임 콜백에서 쓰는 값은 ref로 들고 있어야 최신 값이 보장된다.
  const phaseRef = useRef<Phase>('ready');
  const baselineRef = useRef(baselineDb);
  baselineRef.current = baselineDb;
  const candleRef = useRef<BalloonState>(initialBalloonState);
  const heatRef = useRef(0);

  const changePhase = (next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  };

  const handleFrame = useCallback(({ samples, sampleRate }: MicFrame) => {
    if (phaseRef.current !== 'playing') return;

    const dt = samples.length / sampleRate;
    const diff = frameToDb(samples) - baselineRef.current;

    candleRef.current = stepBalloon(candleRef.current, diff, dt);
    setDbDiff(diff);
    setCandle(candleRef.current);

    // 유지 시간이 목표에 가까울수록 heat가 1에 가까워진다. 내려갈 때는 서서히.
    const wantHeat = Math.min(
      1,
      candleRef.current.holdTime / TARGET_HOLD_SECONDS,
    );
    heatRef.current =
      wantHeat >= heatRef.current
        ? wantHeat
        : Math.max(wantHeat, heatRef.current - COOL_PER_SEC * dt);
    setHeat(heatRef.current);

    if (candleRef.current.passed) {
      changePhase('passed');
      void stop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { start, stop, error } = useMic(handleFrame);

  const resetState = () => {
    candleRef.current = initialBalloonState;
    setCandle(initialBalloonState);
    heatRef.current = 0;
    setHeat(0);
  };

  const startGame = async () => {
    resetState();
    setDbDiff(0);
    changePhase('playing');
    await start();
  };

  const reset = async () => {
    await stop();
    resetState();
    changePhase('ready');
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>촛불 달구기</Text>

      <Text style={styles.guide}>
        {phase === 'ready' &&
          `"아~" 소리를 기준보다 크게(+${TARGET_DELTA_DB}dB 이상) ${TARGET_HOLD_SECONDS}초 동안 유지하면 불꽃이 뜨거워져요.`}
        {phase === 'playing' &&
          `기준보다 크게(+${TARGET_DELTA_DB}dB 이상) ${TARGET_HOLD_SECONDS}초 동안 유지하세요.`}
        {phase === 'passed' && '가장 뜨거운 불꽃이 됐어요! 🎉'}
      </Text>

      <View style={styles.candleArea}>
        {/* 불꽃: 크기는 고정, 색이 온도에 따라 변한다 */}
        <View style={styles.flameArea}>
          <View
            style={[styles.flameOuter, { backgroundColor: flameColor(heat) }]}
          >
            <View
              style={[
                styles.flameInner,
                { backgroundColor: flameCoreColor(heat) },
              ]}
            />
          </View>
        </View>
        <View style={styles.wick} />
        <View style={styles.wax} />
      </View>

      <View>
        <Text style={styles.info}>
          기준 {baselineDb.toFixed(1)} dB
          {phase !== 'ready' &&
            ` / 현재 차이 ${dbDiff >= 0 ? '+' : ''}${dbDiff.toFixed(1)} dB`}
        </Text>
        {phase !== 'ready' && (
          <>
            <Text style={styles.info}>
              유지 {candle.holdTime.toFixed(1)} / {TARGET_HOLD_SECONDS}초
            </Text>
            <Text style={styles.info}>불꽃 온도 약 {flameKelvin(heat)}K</Text>
          </>
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
  candleArea: {
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  flameArea: {
    height: FLAME_HEIGHT + 10,
    justifyContent: 'flex-end',
    alignItems: 'center',
  },
  flameOuter: {
    width: FLAME_WIDTH,
    height: FLAME_HEIGHT,
    borderRadius: 100,
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingBottom: 4,
  },
  flameInner: {
    width: FLAME_WIDTH * 0.5,
    height: FLAME_HEIGHT * 0.55,
    borderRadius: 100,
  },
  wick: {
    width: 4,
    height: 16,
    backgroundColor: '#424242',
  },
  wax: {
    width: 70,
    height: 110,
    backgroundColor: '#fff3e0',
    borderColor: '#d7ccc8',
    borderWidth: 2,
    borderBottomLeftRadius: 8,
    borderBottomRightRadius: 8,
  },
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
