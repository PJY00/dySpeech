// 8. 기록 화면 (진행 추이). 위쪽에 최근 회차 카드, 아래에 서버 진전도(기준선 대비 변화)와 회차별 기록.
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { LineChart, type ChartSeries } from '@/components/LineChart';
import { RecordRow } from '@/components/RecordRow';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { SessionCards } from '@/components/SessionCards';
import { domains, type DomainId } from '@/data/domains';
import type { MetricName } from '@/session';
import { L, useLang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { metricLabel } from '@/training/labels';
import { useTraining } from '@/training/TrainingProvider';
import { findMetric, percentSeries, sessionRows, useHistory } from '@/training/useHistory';

type Filter = 'all' | DomainId;

const LINE_COLORS = ['#2FAF97', '#4A7CF0', '#8E5CF0'];

export default function RecordsScreen() {
  useLang();
  const filterOptions: { value: Filter; label: string }[] = [
    { value: 'all', label: L('전체', 'All') },
    ...domains.map((d) => ({ value: d.id as Filter, label: d.shortTitle })),
  ];
  const [filter, setFilter] = useState<Filter>('all');
  const { cards } = useTraining();
  const { history, loading, error } = useHistory();

  const metrics: { metric: MetricName; color: string }[] =
    filter === 'all'
      ? domains.map((d) => ({ metric: d.metrics[0], color: d.color }))
      : (domains.find((d) => d.id === filter)?.metrics ?? []).map((metric, i) => ({ metric, color: LINE_COLORS[i % LINE_COLORS.length] }));
  const series: ChartSeries[] = metrics
    .map(({ metric, color }) => ({ label: metricLabel(metric), color, values: percentSeries(findMetric(history, metric)) }))
    .filter((s) => s.values.length > 0);
  const count = Math.max(0, ...series.map((s) => s.values.length));

  const rows = sessionRows(history);
  const recent = rows.map((row, i) => ({ row, index: i + 1 })).reverse().slice(0, 3);

  return (
    <Screen
      header={
        <ScreenHeader
          title={L('기록', 'Records')}
          back={false}
          right={
            <Pressable onPress={() => router.push('/clinician')} hitSlop={8}>
              <Text style={styles.demoLink}>{L('임상 기록', 'Clinical')}</Text>
            </Pressable>
          }
        />
      }
    >
      {cards.length > 0 ? <SessionCards cards={cards} /> : null}

      <SegmentedTabs options={filterOptions} value={filter} onChange={setFilter} />

      <Card>
        <Text style={styles.sectionTitle}>{L('지표별 변화 추이 (기준선 대비 %)', 'Change by metric (% vs. baseline)')}</Text>
        {series.length > 0 ? (
          <LineChart series={series} xLabels={count > 1 ? [L('1회차', 'S1'), L(`${count}회차`, `S${count}`)] : [L('1회차', 'S1')]} />
        ) : (
          <Text style={styles.empty}>{loading ? L('불러오는 중…', 'Loading…') : (error ?? L('아직 서버 기록이 없어요.', 'No server records yet.'))}</Text>
        )}
      </Card>

      <Card>
        <View style={styles.cardHeader}>
          <Text style={styles.sectionTitle}>{L('최근 기록', 'Recent records')}</Text>
          <Pressable onPress={() => router.push('/history')} hitSlop={8}>
            <Text style={styles.link}>{L('전체 보기 ›', 'See all ›')}</Text>
          </Pressable>
        </View>
        {recent.length > 0 ? (
          recent.map(({ row, index }) => <RecordRow key={row.sessionId} row={row} index={index} />)
        ) : (
          <Text style={styles.empty}>{L('아직 기록이 없어요.', 'No records yet.')}</Text>
        )}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  demoLink: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.primary,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  link: {
    fontSize: 12,
    color: colors.subText,
  },
  empty: {
    fontSize: 13,
    color: colors.mutedText,
    paddingVertical: spacing.sm,
  },
});
