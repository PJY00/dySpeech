// 풍선 불기(game = balloon, 호흡 영역). 라운드 하나 = 풍선 하나.
// 기준 음량보다 목표만큼 크게 소리를 내면 풍선이 커지고, 목표 시간만큼 이어서 유지하면 끝.
// 판정과 라운드 진행은 useBalloonRound가 하고, 여기서는 풍선을 그린다.
import { useEffect, useRef } from 'react';
import { Animated, Easing, StyleSheet, Text, View } from 'react-native';

import { MAX_LEVEL, ROUND_SECONDS } from './balloonLogic';
import { L, useLang } from '../../i18n';
import type { BalloonGameProps } from './gameProps';
import { Button, common, LevelBadge, NoticeFrame, ResultPanel } from './RoundPanel';
import { ENDING_MS, useBalloonRound } from './useBalloonRound';

const BALLOON_MIN_SCALE = 0.3;
const BALLOON_MAX_SCALE = 1.6;
const BALLOON_BASE_SIZE = 160;

export function BalloonGameScreen(props: BalloonGameProps) {
  useLang();
  const TITLE = L('풍선 불기', 'Blow Up the Balloon');
  const round = useBalloonRound({ ...props, game: 'balloon' });
  const { phase, state, target, lastRun } = round;

  const scale = BALLOON_MIN_SCALE + (BALLOON_MAX_SCALE - BALLOON_MIN_SCALE) * state.size;
  const scaleAnim = useRef(new Animated.Value(scale)).current;
  useEffect(() => {
    if (phase === 'ending' && state.passed) {
      // 목표를 채우면 끝나는 동안 풍선이 천천히 끝까지 부푼다(천천히 커지다 보니 목표 시간에는 아직 덜 커져 있어서)
      Animated.timing(scaleAnim, {
        toValue: BALLOON_MAX_SCALE,
        duration: ENDING_MS,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }).start();
    } else if (phase !== 'ending') {
      scaleAnim.setValue(scale);
    }
  }, [phase, scale, state.passed, scaleAnim]);

  const notice = NoticeFrame({ title: TITLE, phase, error: round.error, onExit: round.exit });
  if (notice) return notice;
  const rule = L(
    `기준보다 +${target.deltaDb}dB 이상 크게 ${target.holdSeconds}초 동안 소리를 이어 가세요.`,
    `Keep your voice at least +${target.deltaDb} dB above your baseline for ${target.holdSeconds} s.`,
  );

  return (
    <View style={common.container}>
      <Text style={common.title}>{TITLE}</Text>
      <Text style={common.level}>
        {L(`단계 ${props.level} / ${MAX_LEVEL} · ${props.roundIndex}번째 연습`, `Level ${props.level} / ${MAX_LEVEL} · Practice #${props.roundIndex}`)}
      </Text>
      <LevelBadge level={props.level} maxPhonationTimeS={props.baselines.maxPhonationTimeS} />

      {phase === 'finished' && lastRun ? (
        <ResultPanel run={lastRun} target={target} onContinue={round.confirm} />
      ) : (
        <>
          <Text style={common.guide}>
            {phase === 'ready' && `${L('"아~" 소리를 내 보세요.', 'Say "ahh" out loud.')} ${rule}`}
            {phase === 'playing' && rule}
            {phase === 'ending' && (state.passed ? L('목표 달성! 🎉', 'You reached the goal! 🎉') : L('잘했어요! 👏', 'Great effort! 👏'))}
          </Text>

          <View style={s.balloonArea}>
            <Animated.View style={[s.balloon, { transform: [{ scale: scaleAnim }] }]} />
          </View>

          {phase === 'playing' && (
            <View>
              <Text style={common.info}>
                {L('기준 대비', 'Vs. baseline')}: {round.dbDiff >= 0 ? '+' : ''}
                {round.dbDiff.toFixed(1)} dB
              </Text>
              <Text style={common.info}>
                {L(
              `유지 ${state.holdTime.toFixed(1)} / ${target.holdSeconds}초 · ${Math.max(0, ROUND_SECONDS - state.elapsed).toFixed(0)}초 남음`,
              `Held ${state.holdTime.toFixed(1)} / ${target.holdSeconds} s · ${Math.max(0, ROUND_SECONDS - state.elapsed).toFixed(0)} s left`,
            )}
              </Text>
            </View>
          )}

          {phase !== 'ending' && (
            <View style={common.buttons}>
              {phase === 'ready' && <Button label={L('시작', 'Start')} onPress={round.start} />}
              <Button label={L('나가기', 'Exit')} onPress={round.exit} light />
            </View>
          )}
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
