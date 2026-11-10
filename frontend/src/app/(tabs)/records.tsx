// 8. 기록 화면 (진행 추이)
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { LineChart, type ChartSeries } from '@/components/LineChart';
import { RecordRow } from '@/components/RecordRow';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { domains, type DomainId } from '@/data/domains';
import { sessionRecords, trendSeries } from '@/data/mock';
import { colors } from '@/theme';

type Filter = 'all' | DomainId;

const filterOptions: { value: Filter; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'breath', label: '호흡 조절' },
  { value: 'rate', label: '발화 속도' },
  { value: 'pitch', label: '목소리' },
];

const allSeries: (ChartSeries & { domain: DomainId })[] = [
  { domain: 'breath', label: '음량', color: domains[0].color, values: trendSeries.db },
  { domain: 'rate', label: '발화 속도', color: domains[1].color, values: trendSeries.rate },
  { domain: 'pitch', label: 'F0', color: domains[2].color, values: trendSeries.f0 },
];

export default function RecordsScreen() {
  const [filter, setFilter] = useState<Filter>('all');
  const series = filter === 'all' ? allSeries : allSeries.filter((s) => s.domain === filter);
  const recent = [...sessionRecords].reverse().slice(0, 3);

  return (
    <Screen
      header={
        <ScreenHeader
          title="기록"
          back={false}
          right={
            <Pressable onPress={() => router.push('/clinician')} hitSlop={8}>
              <Text style={styles.demoLink}>임상 기록(데모)</Text>
            </Pressable>
          }
        />
      }
    >
      <SegmentedTabs options={filterOptions} value={filter} onChange={setFilter} />

      <Card>
        <Text style={styles.sectionTitle}>지표별 변화 추이 (%)</Text>
        <LineChart series={series} xLabels={['1회차', '3회차', '5회차', '8회차']} />
      </Card>

      <Card>
        <View style={styles.cardHeader}>
          <Text style={styles.sectionTitle}>최근 기록</Text>
          <Pressable onPress={() => router.push('/history')} hitSlop={8}>
            <Text style={styles.link}>전체 보기 ›</Text>
          </Pressable>
        </View>
        {recent.map((record) => (
          <RecordRow key={record.session} record={record} />
        ))}
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
});
