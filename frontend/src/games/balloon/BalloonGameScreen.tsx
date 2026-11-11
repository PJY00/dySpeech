// 풍선 불기(game = balloon, 호흡 영역). 라운드 하나 = 풍선 하나.
// 기준 음량보다 목표만큼 크게 소리를 내면 풍선이 커지고, 목표 시간만큼 유지하면 통과.
// 판정과 라운드 진행은 useBalloonRound가 하고, 여기서는 풍선을 그린다.
import { StyleSheet, Text, View } from 'react-native';

import type { BalloonGameProps } from './gameProps';
import { MAX_LEVEL, ROUND_SECONDS } from './balloonLogic';
import { Button, common, NoticeFrame, ResultPanel } from './RoundPanel';
import { useBalloonRound } from './useBalloonRound';

const BALLOON_MIN_SCALE = 0.3;
const BALLOON_MAX_SCALE = 1.6;
const BALLOON_BASE_SIZE = 160;

export function BalloonGameScreen(props: BalloonGameProps) {
  const round = useBalloonRound({ ...props, game: 'balloon' });
  const { phase, state, target, lastRun } = round;

  const notice = NoticeFrame({ title: '풍선 불기', phase, error: round.error, onExit: round.exit });
  if (notice) return notice;

  const scale = BALLOON_MIN_SCALE + (BALLOON_MAX_SCALE - BALLOON_MIN_SCALE) * state.size;
  const rule = `기준보다 +${target.deltaDb}dB 이상으로 ${target.holdSeconds}초 동안 유지하세요.`;

  return (
    <View style={common.container}>
      <Text style={common.title}>풍선 불기</Text>
      <Text style={common.level}>
        단계 {props.level} / {MAX_LEVEL} · {props.roundIndex}번째 연습
      </Text>

      {phase === 'finished' && lastRun ? (
        <ResultPanel run={lastRun} target={target} onContinue={round.confirm} />
      ) : (
        <>
          <Text style={common.guide}>{phase === 'ready' ? `"아~" 소리를 내 보세요. ${rule}` : rule}</Text>

          <View style={s.balloonArea}>
            <View style={[s.balloon, { transform: [{ scale }] }]} />
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
            </View>
          )}

          <View style={common.buttons}>
            {phase === 'ready' && <Button label="시작" onPress={round.start} />}
            <Button label="나가기" onPress={round.exit} light />
          </View>
        </>
      )}
    </View>
  );
}

const s = StyleSheet.create({
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
});
