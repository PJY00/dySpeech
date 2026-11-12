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
import { colors, spacing } from '@/theme';
import { passRateFor, roundPassed } from '@/training/progress';
import { useTraining } from '@/training/TrainingProvider';

const DECISION_TEXT: Record<Decision, string> = {
  up: '다음 라운드는 한 단계 올라가요',
  down: '다음 라운드는 한 단계 쉬워져요',
  hold: '다음 라운드도 같은 단계예요',
  deferred: '이번 라운드는 판정을 미뤘어요',
};

export default function GameCompleteScreen() {
  const { lastRound } = useTraining();

  if (!lastRound) {
    return <PlaceholderScreen title="게임 완료" description="표시할 라운드가 없어요." />;
  }

  const { entry, run, nextLevel } = lastRound;
  const domain = domains.find((d) => d.id === entry.area) ?? domains[0];
  const passed = roundPassed(run);

  const metrics = [
    { label: '성공률', value: `${Math.round(run.success_rate_pct)}`, unit: '%', target: `통과 ${passRateFor(entry.game)}% 이상`, ok: passed },
    { label: '이번 단계', value: `${run.level}`, unit: `/ ${entry.maxLevel}`, target: `${run.round_index}번째 라운드`, ok: passed },
    { label: '다음 단계', value: `${nextLevel}`, unit: `/ ${entry.maxLevel}`, target: DECISION_TEXT[run.decision], ok: passed },
  ];

  return (
    <Screen
      header={<ScreenHeader title={entry.title} back={false} />}
      footer={
        <View style={styles.actions}>
          <PrimaryButton label={`다음 라운드 (단계 ${nextLevel})`} onPress={() => router.replace(`/games/play/${entry.game}`)} />
          <View style={styles.secondaryRow}>
            <Pressable onPress={() => router.dismissTo('/games')} hitSlop={8}>
              <Text style={styles.secondary}>다른 게임</Text>
            </Pressable>
            <Pressable onPress={() => router.replace('/session/finishing')} hitSlop={8}>
              <Text style={styles.secondary}>오늘 훈련 끝</Text>
            </Pressable>
          </View>
        </View>
      }
    >
      <View style={styles.hero}>
        <View style={[styles.icon, { backgroundColor: domain.tint }]}>
          <MaterialCommunityIcons name={domain.icon} size={56} color={domain.color} />
        </View>
        <Text style={styles.title}>{passed ? '게임을 완료했어요!' : '아쉬워요, 다시 해볼까요?'}</Text>
        <Text style={styles.sub}>{passed ? '지금처럼만 꾸준히 연습해요!' : '목표에 조금 못 미쳤어요. 한 번 더 도전해봐요!'}</Text>
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
        {run.decision_reason ? <Text style={styles.reason}>{run.decision_reason}</Text> : null}
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
