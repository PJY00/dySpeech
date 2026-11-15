// [임시] 아직 연결되지 않은 게임(풍선·촛불·우주선·롤러코스터) 자리.
// 게임 연결 약속(GameProps)대로 라운드 결과를 돌려주되, 결과는 버튼으로 고른다.
// 임시 라운드는 서버 회차에 넣지 않는다(TrainingProvider.recordRound). 오늘 완료 현황과 단계 기록에만 반영된다.
// 실제 게임을 registry.ts에 등록하면 그 게임은 이 목록에서 자동으로 빠진다.
import { StyleSheet, Text, View } from 'react-native';

import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { uuidv4 } from '@/session';
import type { Decision } from '@/session';
import { colors, spacing } from '@/theme';
import type { GameArea, GameEntry, GameName, GameProps } from '@/training/gameTypes';
import { findGame } from '../registry';

export type TempGameEntry = GameEntry & { temp: true };

const TEMP_MAX_LEVEL = 5;

const OUTCOMES: { label: string; success: number; decision: Decision }[] = [
  { label: '통과 · 다음 단계로 올림', success: 100, decision: 'up' },
  { label: '통과 · 같은 단계 유지', success: 85, decision: 'hold' },
  { label: '실패 · 같은 단계 유지', success: 60, decision: 'hold' },
  { label: '실패 · 한 단계 내림', success: 30, decision: 'down' },
  { label: '판정 미룸', success: 0, decision: 'deferred' },
];

function makeTempGame(game: GameName, title: string) {
  return function TempGame({ level, roundIndex, onRoundEnd, onExit }: GameProps) {
    const startedAt = new Date().toISOString();
    const finish = (success: number, decision: Decision) =>
      onRoundEnd({
        game_run_id: uuidv4(),
        game,
        level,
        round_index: roundIndex,
        started_at: startedAt,
        success_rate_pct: success,
        decision,
        decision_reason: '임시 게임: 버튼으로 고른 결과',
        params: { temp: true },
      });

    return (
      <Screen header={<ScreenHeader title={`${title} (임시)`} />}>
        <View style={styles.info}>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.sub}>
            단계 {level} / {TEMP_MAX_LEVEL} · {roundIndex}번째 라운드
          </Text>
          <Text style={styles.note}>아직 연결되지 않은 게임이에요. 결과를 골라 주세요.{'\n'}서버로 보내지 않아요.</Text>
        </View>
        <View style={styles.buttons}>
          {OUTCOMES.map((o) => (
            <PrimaryButton key={o.label} label={`${o.label} (${o.success}%)`} chevron={false} onPress={() => finish(o.success, o.decision)} />
          ))}
        </View>
        <Text style={styles.exit} onPress={onExit}>
          나가기 (기록 안 함)
        </Text>
      </Screen>
    );
  };
}

const TEMP_CANDIDATES: { game: GameName; area: GameArea; title: string; description: string }[] = [
  { game: 'balloon', area: 'breath', title: '풍선 불기', description: '목표 음량을 유지해 풍선을 키워요' },
  { game: 'candle', area: 'breath', title: '촛불 지키기', description: '고른 목소리로 촛불을 지켜요' },
  { game: 'spaceship', area: 'intonation', title: '우주선 게임', description: '목소리 높낮이로 우주선을 움직여요' },
  { game: 'roller_coaster', area: 'intonation', title: '롤러코스터 게임', description: '억양 곡선을 따라 달려요' },
];

/** 등록부에 아직 없는 게임만 임시 게임으로 둔다 */
export const TEMP_GAMES: TempGameEntry[] = TEMP_CANDIDATES.filter((c) => !findGame(c.game)).map((c) => ({
  ...c,
  maxLevel: TEMP_MAX_LEVEL,
  temp: true,
  component: makeTempGame(c.game, c.title),
}));

export function tempGamesInArea(area: GameArea): TempGameEntry[] {
  return TEMP_GAMES.filter((g) => g.area === area);
}

export function findTempGame(game: string): TempGameEntry | undefined {
  return TEMP_GAMES.find((g) => g.game === game);
}

export function isTempGame(entry: GameEntry): entry is TempGameEntry {
  return (entry as Partial<TempGameEntry>).temp === true;
}

const styles = StyleSheet.create({
  info: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.lg,
  },
  title: {
    fontSize: 22,
    fontWeight: '800',
    color: colors.text,
  },
  sub: {
    fontSize: 14,
    color: colors.subText,
  },
  note: {
    fontSize: 12,
    color: colors.mutedText,
    textAlign: 'center',
  },
  buttons: {
    gap: spacing.sm,
  },
  exit: {
    textAlign: 'center',
    fontSize: 14,
    fontWeight: '600',
    color: colors.subText,
    paddingVertical: spacing.md,
  },
});
