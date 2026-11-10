// 7. 훈련 결과 요약 화면
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { domains } from '@/data/domains';
import { sessionSummary, trendSeries } from '@/data/mock';
import { useTodayProgress } from '@/state/today-progress';
import { colors, spacing } from '@/theme';

type Metric = 'db' | 'rate' | 'f0';

const metricOptions: { value: Metric; label: string }[] = [
  { value: 'db', label: '음량' },
  { value: 'rate', label: '발화 속도' },
  { value: 'f0', label: 'F0' },
];

const metricColors: Record<Metric, string> = {
  db: domains[0].color,
  rate: domains[1].color,
  f0: domains[2].color,
};

export default function SessionResultScreen() {
  const [metric, setMetric] = useState<Metric>('db');
  const { completedCount, totalCount } = useTodayProgress();
  const label = metricOptions.find((option) => option.value === metric)?.label ?? '';

  return (
    <Screen
      header={<ScreenHeader title="훈련 결과" />}
      footer={<PrimaryButton label="확인" chevron={false} onPress={() => router.dismissTo('/')} />}
    >
      <View style={styles.greeting}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={26} color={colors.primary} />
        </View>
        <View style={styles.greetingText}>
          <Text style={styles.hello}>오늘도 수고했어요!</Text>
          <Text style={styles.sub}>지금까지의 훈련 기록을 확인해보세요.</Text>
        </View>
      </View>

      <Card>
        <Text style={styles.sectionTitle}>이번 세션 요약</Text>
        <View style={styles.summary}>
          <Summary label="총 훈련 시간" value={sessionSummary.trainingTime} />
          <Summary
            label="완료한 게임"
            value={`${completedCount} / ${totalCount}`}
          />
          <Summary label="평균 점수" value={`${sessionSummary.score}점`} />
        </View>
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>지표 변화</Text>
        <SegmentedTabs options={metricOptions} value={metric} onChange={setMetric} />
        <LineChart
          series={[{ label, color: metricColors[metric], values: trendSeries[metric] }]}
          xLabels={['1회차', '3회차', '5회차', '8회차']}
          showLegend={false}
        />
      </Card>
    </Screen>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.summaryItem}>
      <Text style={styles.summaryLabel}>{label}</Text>
      <Text style={styles.summaryValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  greeting: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  greetingText: {
    flex: 1,
  },
  hello: {
    fontSize: 17,
    fontWeight: '700',
    color: colors.text,
  },
  sub: {
    fontSize: 13,
    color: colors.subText,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  summary: {
    flexDirection: 'row',
  },
  summaryItem: {
    flex: 1,
    alignItems: 'center',
    gap: spacing.xs,
  },
  summaryLabel: {
    fontSize: 12,
    color: colors.subText,
  },
  summaryValue: {
    fontSize: 17,
    fontWeight: '800',
    color: colors.text,
  },
});
