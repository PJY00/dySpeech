// 9. 임상가용 화면 (데모). 기록 탭과 설정의 데모 버튼으로 들어온다.
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { Card } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { domains } from '@/data/domains';
import { patient, sessionRecords } from '@/data/mock';
import { colors, spacing } from '@/theme';

type ClinicianView = 'trend' | 'sessions';

const viewOptions: { value: ClinicianView; label: string }[] = [
  { value: 'trend', label: '지표 추이' },
  { value: 'sessions', label: '회차별 기록' },
];

export default function ClinicianScreen() {
  const [view, setView] = useState<ClinicianView>('sessions');
  const first = sessionRecords[0].session;
  const last = sessionRecords[sessionRecords.length - 1].session;

  return (
    <Screen
      header={
        <ScreenHeader
          title="임상 기록"
          right={<Text style={styles.range}>{`${first}회차 ~ ${last}회차`}</Text>}
        />
      }
    >
      <Card style={styles.patient}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={26} color={colors.primary} />
        </View>
        <View style={styles.patientText}>
          <Text style={styles.name}>
            {patient.name} ({patient.sex}/{patient.age})
          </Text>
          <Text style={styles.meta}>{patient.diagnosis}</Text>
          <Text style={styles.meta}>시작일 {patient.startedAt}</Text>
        </View>
      </Card>

      <SegmentedTabs options={viewOptions} value={view} onChange={setView} />

      {view === 'sessions' ? (
        <Card>
          <View style={[styles.tableRow, styles.tableHead]}>
            <HeadCell title="회차" />
            <HeadCell title="음량" unit="(dB)" />
            <HeadCell title="발화 속도" unit="(음절/초)" />
            <HeadCell title="무음 비율" unit="(%)" />
          </View>
          {sessionRecords.map((record) => (
            <View key={record.session} style={styles.tableRow}>
              <Text style={styles.cell}>{record.session}</Text>
              <Text style={styles.cell}>{record.db.toFixed(1)}</Text>
              <Text style={styles.cell}>{record.rate.toFixed(2)}</Text>
              <Text style={styles.cell}>{record.pauseRatio}%</Text>
            </View>
          ))}
        </Card>
      ) : (
        <Card>
          <LineChart
            series={[
              { label: '음량', color: domains[0].color, values: sessionRecords.map((r) => r.db) },
            ]}
            xLabels={sessionRecords.map((r) => `${r.session}`)}
          />
          <LineChart
            series={[
              { label: '발화 속도', color: domains[1].color, values: sessionRecords.map((r) => r.rate) },
            ]}
            xLabels={sessionRecords.map((r) => `${r.session}`)}
          />
        </Card>
      )}
    </Screen>
  );
}

function HeadCell({ title, unit }: { title: string; unit?: string }) {
  return (
    <View style={styles.headCell}>
      <Text style={styles.headTitle}>{title}</Text>
      {unit ? <Text style={styles.headUnit}>{unit}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  range: {
    fontSize: 12,
    color: colors.subText,
  },
  patient: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  avatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  patientText: {
    flex: 1,
    gap: 2,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.text,
  },
  meta: {
    fontSize: 12,
    color: colors.subText,
  },
  tableRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
  },
  tableHead: {
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headCell: {
    flex: 1,
    alignItems: 'center',
  },
  headTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.subText,
  },
  headUnit: {
    fontSize: 10,
    color: colors.mutedText,
  },
  cell: {
    flex: 1,
    textAlign: 'center',
    fontSize: 13,
    color: colors.text,
  },
});
