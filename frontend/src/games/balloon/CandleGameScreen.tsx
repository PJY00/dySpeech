// 촛불 지키기(game = candle, 호흡 영역). 판정은 풍선과 같고 표시 객체만 촛불이다.
// 센 불꽃(푸른 흰색)으로 시작한다. 소리가 목표만큼 이어지면 불이 유지되고, 못 미치면 불꽃이 작아지고
// 식은 색(주황 → 어두운 빨강)이 되다가 꺼진다. 불이 약해질수록 배경이 어두워지지만 초는 계속 보인다.
// 소리가 돌아오면 불꽃도 배경도 다시 밝아진다. 목표 시간만큼 이어서 유지하면 끝.
import { StyleSheet, Text, View } from 'react-native';

import { MAX_LEVEL, ROUND_SECONDS } from './balloonLogic';
import { FLAME_OUT_BELOW, flameColor, flameCoreColor, flameKelvin, sceneColor } from './flameColor';
import { L, useLang } from '../../i18n';
import type { BalloonGameProps } from './gameProps';
import { Button, common, LevelBadge, NoticeFrame, ResultPanel } from './RoundPanel';
import { useBalloonRound } from './useBalloonRound';

const FLAME_WIDTH = 70;
const FLAME_HEIGHT = 130;

export function CandleGameScreen(props: BalloonGameProps) {
  useLang();
  const TITLE = L('촛불 지키기', 'Keep the Candle Lit');
  const round = useBalloonRound({ ...props, game: 'candle' });
  const { phase, state, target, lastRun } = round;

  const notice = NoticeFrame({ title: TITLE, phase, error: round.error, onExit: round.exit });
  if (notice) return notice;

  // 불의 세기는 풍선의 크기와 같은 값이다(가득 1 → 꺼짐 0)
  const heat = state.size;
  const lit = heat > FLAME_OUT_BELOW;
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
        <Text style={common.guide}>
          {phase === 'ready' && `${L('"아~" 소리를 이어 내서 촛불이 꺼지지 않게 해 주세요.', "Say \"ahh\" and keep going so the candle doesn't go out.")} ${rule}`}
          {phase === 'playing' && `${L('촛불을 지켜 주세요.', 'Keep the candle lit.')} ${rule}`}
          {phase === 'ending' && (state.passed ? L('목표 달성! 🎉', 'You reached the goal! 🎉') : L('잘했어요! 👏', 'Great effort! 👏'))}
        </Text>
      )}

      {/* 배경은 불이 약해질수록 어두워지고, 가장 어두울 때도 초가 보이도록 최소 밝기를 둔다 */}
      <View style={[s.scene, { backgroundColor: sceneColor(heat) }]}>
        <View style={s.candleArea}>
          <View style={s.flameArea}>
            {lit && (
              <View
                style={[
                  s.flameOuter,
                  { width: FLAME_WIDTH * heat, height: FLAME_HEIGHT * heat, backgroundColor: flameColor(heat) },
                ]}
              >
                <View
                  style={[
                    s.flameInner,
                    { width: FLAME_WIDTH * heat * 0.5, height: FLAME_HEIGHT * heat * 0.55, backgroundColor: flameCoreColor(heat) },
                  ]}
                />
              </View>
            )}
          </View>
          <View style={s.wick} />
          <View style={s.wax} />
        </View>
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
          {lit && <Text style={common.info}>
              {L('불꽃 온도', 'Flame temperature')} ≈ {flameKelvin(heat)} K
            </Text>}
        </View>
      )}

      {phase !== 'finished' && phase !== 'ending' && (
        <View style={common.buttons}>
          {phase === 'ready' && <Button label={L('시작', 'Start')} onPress={round.start} />}
          <Button label={L('나가기', 'Exit')} onPress={round.exit} light />
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  scene: { alignSelf: 'stretch', alignItems: 'center', justifyContent: 'flex-end', paddingTop: 16, borderRadius: 16 },
  candleArea: { alignItems: 'center', justifyContent: 'flex-end' },
  flameArea: { height: FLAME_HEIGHT + 10, justifyContent: 'flex-end', alignItems: 'center' },
  flameOuter: { borderRadius: 100, alignItems: 'center', justifyContent: 'flex-end', paddingBottom: 4 },
  flameInner: { borderRadius: 100 },
  wick: { width: 4, height: 16, backgroundColor: '#6d4c41' },
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
