// 목표 음량 이상을 유지할수록 불꽃 색이 실제 불꽃 온도 순서(어두운 빨강 → 주황 → 노랑 → 흰색 → 푸른 흰색)로 변하고,
// 목표 시간만큼 유지해 목표 색에 도달하면 통과.
import { useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type { BalloonGameProps } from './gameProps';
import { BalloonState, MAX_LEVEL, ROUND_SECONDS } from './balloonLogic';
import { flameColor, flameCoreColor, flameKelvin } from './flameColor';
import { Button, common, NoticeFrame, ResultPanel } from './RoundPanel';
import { useBalloonRound } from './useBalloonRound';

const FLAME_WIDTH = 70;
const FLAME_HEIGHT = 130;
/** 목표 음량 아래로 떨어졌을 때 색이 식는 속도 */
const COOL_PER_SEC = 0.5;

export function CandleGameScreen(props: BalloonGameProps) {
  const [heat, setHeat] = useState(0);
  const heatRef = useRef(0);
  const holdSecondsRef = useRef(1);

  const round = useBalloonRound({
    ...props,
    game: 'candle',
    onStep: (s: BalloonState, dt: number) => {
      const want = Math.min(1, s.holdTime / holdSecondsRef.current);
      heatRef.current = want >= heatRef.current ? want : Math.max(want, heatRef.current - COOL_PER_SEC * dt);
      setHeat(heatRef.current);
    },
  });
  const { phase, state, target, lastRun } = round;
  holdSecondsRef.current = target.holdSeconds;

  const notice = NoticeFrame({ title: '촛불 달구기', phase, error: round.error, onExit: round.exit });
  if (notice) return notice;

  const start = async () => {
    heatRef.current = 0;
    setHeat(0);
    await round.start();
  };
  const rule = `기준보다 +${target.deltaDb}dB 이상으로 ${target.holdSeconds}초 동안 유지하세요.`;

  return (
    <View style={common.container}>
      <Text style={common.title}>촛불 달구기</Text>
      <Text style={common.level}>
        단계 {props.level} / {MAX_LEVEL} · {props.roundIndex}번째 연습
      </Text>

      {phase === 'finished' && lastRun ? (
        <ResultPanel run={lastRun} target={target} onContinue={round.confirm} />
      ) : (
        <Text style={common.guide}>
          {phase === 'ready' ? `"아~" 소리를 내 보세요. ${rule} 유지할수록 불꽃이 뜨거워져요.` : rule}
        </Text>
      )}

      <View style={s.candleArea}>
        {/* 불꽃: 크기는 고정, 색이 온도에 따라 변한다 */}
        <View style={s.flameArea}>
          <View style={[s.flameOuter, { backgroundColor: flameColor(heat) }]}>
            <View style={[s.flameInner, { backgroundColor: flameCoreColor(heat) }]} />
          </View>
        </View>
        <View style={s.wick} />
        <View style={s.wax} />
      </View>

      {phase === 'playing' && (
        <View>
          <Text style={common.info}>
            현재 차이 {round.dbDiff >= 0 ? '+' : ''}
            {round.dbDiff.toFixed(1)} dB
          </Text>
          <Text style={common.info}>
            유지 {state.holdTime.toFixed(1)} / {target.holdSeconds}초 · 남은 시간 {Math.max(0, ROUND_SECONDS - state.elapsed).toFixed(0)}초
          </Text>
          <Text style={common.info}>불꽃 온도 약 {flameKelvin(heat)}K</Text>
        </View>
      )}

      {phase !== 'finished' && (
        <View style={common.buttons}>
          {phase === 'ready' && <Button label="시작" onPress={start} />}
          <Button label="나가기" onPress={round.exit} light />
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  candleArea: { alignItems: 'center', justifyContent: 'flex-end' },
  flameArea: { height: FLAME_HEIGHT + 10, justifyContent: 'flex-end', alignItems: 'center' },
  flameOuter: {
    width: FLAME_WIDTH,
    height: FLAME_HEIGHT,
    borderRadius: 100,
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingBottom: 4,
  },
  flameInner: { width: FLAME_WIDTH * 0.5, height: FLAME_HEIGHT * 0.55, borderRadius: 100 },
  wick: { width: 4, height: 16, backgroundColor: '#424242' },
  wax: {
    width: 70,
    height: 110,
    backgroundColor: '#fff3e0',
    borderColor: '#d7ccc8',
    borderWidth: 2,
    borderBottomLeftRadius: 8,
    borderBottomRightRadius: 8,
  },
});
