// 회차 한 줄: 회차·날짜와 진전도 지표 3개(평균 음량 변화, 조음속도, 무음 비율)
import { StyleSheet, Text, View } from 'react-native';

import { colors, spacing } from '@/theme';
import type { SessionRow } from '@/training/useHistory';

export function RecordRow({ row, index }: { row: SessionRow; index: number }) {
  const date = new Date(row.startedAt);
  const v = row.values;
  return (
    <View style={styles.row}>
      <View style={styles.title}>
        <Text style={styles.session}>{index}회차</Text>
        <Text style={styles.date}>
          {date.getFullYear()}.{String(date.getMonth() + 1).padStart(2, '0')}.{String(date.getDate()).padStart(2, '0')}
        </Text>
      </View>
      <RecordValue label="음량 변화" value={v.mean_level_change !== undefined ? `${v.mean_level_change.toFixed(1)} dB` : '–'} />
      <RecordValue label="조음속도" value={v.articulation_rate !== undefined ? v.articulation_rate.toFixed(2) : '–'} />
      <RecordValue label="무음 비율" value={v.pause_ratio !== undefined ? `${v.pause_ratio.toFixed(0)}%` : '–'} />
    </View>
  );
}

function RecordValue({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.value}>
      <Text style={styles.valueLabel}>{label}</Text>
      <Text style={styles.valueText}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  title: {
    flex: 1.2,
  },
  session: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.text,
  },
  date: {
    fontSize: 11,
    color: colors.mutedText,
  },
  value: {
    flex: 1,
    alignItems: 'center',
  },
  valueLabel: {
    fontSize: 11,
    color: colors.subText,
  },
  valueText: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
  },
});
