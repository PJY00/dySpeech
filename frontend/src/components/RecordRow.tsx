import { StyleSheet, Text, View } from 'react-native';

import type { SessionRecord } from '@/data/mock';
import { colors, spacing } from '@/theme';

export function RecordRow({ record }: { record: SessionRecord }) {
  return (
    <View style={styles.row}>
      <View style={styles.title}>
        <Text style={styles.session}>{record.session}회차</Text>
        <Text style={styles.date}>{record.date}</Text>
      </View>
      <RecordValue label="음량" value={`${record.db} dB`} />
      <RecordValue label="발화 속도" value={`${record.rate}`} />
      <RecordValue label="무음 비율" value={`${record.pauseRatio}%`} />
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
