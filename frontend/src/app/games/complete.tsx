// 6. 게임 완료(라운드 끝) 화면. 통과 여부는 progress.ts 규칙(성공률 기준)으로 보여 준다.
// 버튼: 다음 라운드(단계 저장값) / 다른 게임 / 오늘 훈련 끝
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { domains } from '@/data/domains';
import type { Decision } from '@/session';
import { L, useLang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { passRateFor, reportsPassed, roundPassed, summarizeRun } from '@/training/progress';
import { useTraining } from '@/training/TrainingProvider';

function decisionText(decision: Decision): string {
  switch (decision) {
    case 'up':
      return L('다음 라운드는 한 단계 올라가요', 'Next round goes up one level');
    case 'down':
      return L('다음 라운드는 한 단계 쉬워져요', 'Next round goes down one level');
    case 'hold':
      return L('다음 라운드도 같은 단계예요', 'Next round stays at this level');
    default:
      return L('이번 라운드는 판정을 미뤘어요', 'No level decision this round');
  }
}

export default function GameCompleteScreen() {
  useLang();
  const { lastRound } = useTraining();

  if (!lastRound) {
    return <PlaceholderScreen title={L('게임 완료', 'Round complete')} description={L('표시할 라운드가 없어요.', 'No round to show.')} />;
  }

  const { entry, run, nextLevel } = lastRound;
  const domain = domains.find((d) => d.id === entry.area) ?? domains[0];
  const passed = roundPassed(summarizeRun(run, entry.area));
  const reason = entry.describeRun?.(run) ?? null;

  const metrics = [
    {
      label: L('성공률', 'Success rate'),
      value: `${Math.round(run.success_rate_pct)}`,
      unit: '%',
      target: reportsPassed(run) ? L('게임 판정 기준', "Game's own criterion") : L(`통과 ${passRateFor(entry.game)}% 이상`, `Pass at ${passRateFor(entry.game)}%+`),
      ok: passed,
    },
    { label: L('이번 단계', 'This level'), value: `${run.level}`, unit: `/ ${entry.maxLevel}`, target: L(`${run.round_index}번째 라운드`, `Round ${run.round_index}`), ok: passed },
    { label: L('다음 단계', 'Next level'), value: `${nextLevel}`, unit: `/ ${entry.maxLevel}`, target: decisionText(run.decision), ok: passed },
  ];

  return (
    <Screen
      header={<ScreenHeader title={entry.title} back={false} />}
      footer={
        <View style={styles.actions}>
          <PrimaryButton label={L(`다음 라운드 (단계 ${nextLevel})`, `Next round (level ${nextLevel})`)} onPress={() => router.replace(`/games/play/${entry.game}`)} />
          <View style={styles.secondaryRow}>
            <Pressable onPress={() => router.dismissTo('/games')} hitSlop={8}>
              <Text style={styles.secondary}>{L('다른 게임', 'Other games')}</Text>
            </Pressable>
            <Pressable onPress={() => router.replace('/session/finishing')} hitSlop={8}>
              <Text style={styles.secondary}>{L('오늘 훈련 끝', 'Finish for today')}</Text>
            </Pressable>
          </View>
        </View>
      }
    >
      <View style={styles.hero}>
        <View style={[styles.icon, { backgroundColor: domain.tint }]}>
          <MaterialCommunityIcons name={domain.icon} size={56} color={domain.color} />
        </View>
        <Text style={styles.title}>{passed ? L('게임을 완료했어요!', 'Round complete!') : L('아쉬워요, 다시 해볼까요?', 'So close. Try again?')}</Text>
        <Text style={styles.sub}>{passed ? L('지금처럼만 꾸준히 연습해요!', 'Keep practicing like this!') : L('목표에 조금 못 미쳤어요. 한 번 더 도전해봐요!', 'Just short of the goal. Give it another go!')}</Text>
      </View>

      <Card>
        {metrics.map((metric, index) => (
          <View key={metric.label} style={[styles.metric, index > 0 && styles.divider]}>
            <View style={styles.metricText}>
              <Text style={styles.metricLabel}>{metric.label}</Text>
              <Text style={styles.metricValue}>
                {metric.value}
                <Text style={styles.metricUnit}> {metric.unit}</Text>
              </Text>
            </View>
            <Text style={styles.target}>{metric.target}</Text>
            {index === 0 ? (
              <Ionicons
                name={metric.ok ? 'checkmark-circle' : 'close-circle'}
                size={22}
                color={metric.ok ? colors.success : colors.mutedText}
              />
            ) : null}
          </View>
        ))}
        {reason ? <Text style={styles.reason}>{reason}</Text> : null}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.md,
  },
  icon: {
    width: 104,
    height: 104,
    borderRadius: 52,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: colors.text,
  },
  sub: {
    fontSize: 14,
    color: colors.subText,
  },
  metric: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.xs,
  },
  divider: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.md,
  },
  metricText: {
    flex: 1,
  },
  metricLabel: {
    fontSize: 13,
    color: colors.subText,
  },
  metricValue: {
    fontSize: 24,
    fontWeight: '800',
    color: colors.text,
  },
  metricUnit: {
    fontSize: 14,
    fontWeight: '600',
  },
  target: {
    fontSize: 12,
    color: colors.mutedText,
  },
  reason: {
    fontSize: 12,
    color: colors.subText,
  },
  actions: {
    gap: spacing.md,
  },
  secondaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  secondary: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.subText,
  },
});
