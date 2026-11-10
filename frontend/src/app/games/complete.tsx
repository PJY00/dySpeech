// 6. 게임 완료 화면. 통과하지 못하면 같은 게임을 다시 시도하게 한다.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';

import { Card } from '@/components/Card';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { domains, findDomain } from '@/data/domains';
import { gameResults } from '@/data/mock';
import { colors, spacing } from '@/theme';

export default function GameCompleteScreen() {
  const params = useLocalSearchParams<{ domain?: string; passed?: string }>();
  const domain = findDomain(params.domain) ?? domains[0];
  const passed = params.passed !== 'false';
  const result = gameResults[domain.id];

  return (
    <Screen
      header={<ScreenHeader title={result.game} />}
      footer={
        <View style={styles.actions}>
          {passed ? (
            <PrimaryButton label="다음 게임으로" onPress={() => router.dismissTo('/games')} />
          ) : (
            <PrimaryButton label="다시 도전하기" onPress={() => router.replace(`/games/${domain.id}`)} />
          )}
          <Pressable onPress={() => router.replace('/session/result')} hitSlop={8}>
            <Text style={styles.finish}>훈련 마치기</Text>
          </Pressable>
        </View>
      }
    >
      <View style={styles.hero}>
        <View style={[styles.icon, { backgroundColor: domain.tint }]}>
          <MaterialCommunityIcons name={domain.icon} size={56} color={domain.color} />
        </View>
        <Text style={styles.title}>{passed ? '게임을 완료했어요!' : '아쉬워요, 다시 해볼까요?'}</Text>
        <Text style={styles.sub}>
          {passed ? '지금처럼만 꾸준히 연습해요!' : '목표에 조금 못 미쳤어요. 한 번 더 도전해봐요!'}
        </Text>
      </View>

      <Card>
        {result.metrics.map((metric, index) => (
          <View key={metric.label} style={[styles.metric, index > 0 && styles.divider]}>
            <View style={styles.metricText}>
              <Text style={styles.metricLabel}>{metric.label}</Text>
              <Text style={styles.metricValue}>
                {metric.value}
                <Text style={styles.metricUnit}> {metric.unit}</Text>
              </Text>
            </View>
            <Text style={styles.target}>{metric.target}</Text>
            <Ionicons
              name={passed && metric.passed ? 'checkmark-circle' : 'close-circle'}
              size={22}
              color={passed && metric.passed ? colors.success : colors.mutedText}
            />
          </View>
        ))}
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
  actions: {
    gap: spacing.md,
    alignItems: 'stretch',
  },
  finish: {
    textAlign: 'center',
    fontSize: 14,
    color: colors.subText,
    fontWeight: '600',
  },
});
