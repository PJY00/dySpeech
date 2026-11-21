// 9. 임상가용 화면. 기록 탭과 설정의 버튼으로 들어온다(계정·권한 없이 누구나 볼 수 있는 확인용 화면).
// 기본은 이 기기 사용자의 실제 기록(서버 진전도). 과거 기록이 없는 시연용으로 '데모 데이터'로 바꿀 수 있다.
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
import { L, useLang } from '@/i18n';
import { colors, radius, spacing } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';
import { sessionRows, useHistory } from '@/training/useHistory';

type Source = 'real' | 'demo';
type ClinicianView = 'trend' | 'sessions';

interface Row {
  session: number;
  date: string;
  levelChange: number | null;
  rate: number | null;
  pauseRatio: number | null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
}

export default function ClinicianScreen() {
  useLang();
  const sourceOptions: { value: Source; label: string }[] = [
    { value: 'real', label: L('실제 기록', 'Real records') },
    { value: 'demo', label: L('데모 데이터', 'Demo data') },
  ];
  const viewOptions: { value: ClinicianView; label: string }[] = [
    { value: 'trend', label: L('지표 추이', 'Trends') },
    { value: 'sessions', label: L('회차별 기록', 'By session') },
  ];
  const sessionName = (n: number) => L(`${n}회차`, `S${n}`);
  const [source, setSource] = useState<Source>('real');
  const [view, setView] = useState<ClinicianView>('sessions');
  const { userKey } = useTraining();
  const { history, loading, error } = useHistory();

  const realRows: Row[] = sessionRows(history).map((r, i) => ({
    session: i + 1,
    date: formatDate(r.startedAt),
    levelChange: r.values.mean_level_change ?? null,
    rate: r.values.articulation_rate ?? null,
    pauseRatio: r.values.pause_ratio ?? null,
  }));
  const demoRows: Row[] = sessionRecords.map((r) => ({
    session: r.session,
    date: r.date,
    levelChange: r.db,
    rate: r.rate,
    pauseRatio: r.pauseRatio,
  }));
  const rows = source === 'real' ? realRows : demoRows;
  const demo = source === 'demo';

  const empty = loading ? L('불러오는 중…', 'Loading…') : (error ?? L('아직 서버 기록이 없어요.', 'No server records yet.'));
  const xLabels = rows.length > 1 ? [sessionName(1), sessionName(rows.length)] : [sessionName(1)];
  const series = (pick: (r: Row) => number | null) => rows.map(pick).filter((v): v is number => v !== null);
  const show = (v: number | null, digits: number, unit = '') => (v === null ? '–' : `${v.toFixed(digits)}${unit}`);

  return (
    <Screen
      header={
        <ScreenHeader
          title={L('임상 기록', 'Clinical records')}
          right={rows.length > 0 ? <Text style={styles.range}>{`${sessionName(1)} ~ ${sessionName(rows.length)}`}</Text> : undefined}
        />
      }
    >
      <SegmentedTabs options={sourceOptions} value={source} onChange={setSource} />
      {demo ? (
        <View style={styles.demoBadge}>
          <Ionicons name="information-circle" size={16} color={colors.warning} />
          <Text style={styles.demoText}>
            {L('데모 데이터예요. 시연용 가짜 기록입니다.', 'Demo data: sample records for demonstration.')}
          </Text>
        </View>
      ) : null}

      <Card style={styles.patient}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={26} color={colors.primary} />
        </View>
        <View style={styles.patientText}>
          {demo ? (
            <>
              <Text style={styles.name}>
                {patient.name} ({patient.sex}/{patient.age})
              </Text>
              <Text style={styles.meta}>{patient.diagnosis}</Text>
              <Text style={styles.meta}>
                {L('시작일', 'Started')} {patient.startedAt}
              </Text>
            </>
          ) : (
            <>
              <Text style={styles.name}>{L('이 기기 사용자', 'User of this device')}</Text>
              <Text style={styles.meta}>
                {L('사용자 ID', 'User ID')} {userKey ? `${userKey.slice(0, 8)}…` : '–'}
              </Text>
              <Text style={styles.meta}>
                {L('시작일', 'Started')} {realRows[0]?.date ?? '–'}
              </Text>
            </>
          )}
        </View>
      </Card>

      <SegmentedTabs options={viewOptions} value={view} onChange={setView} />

      {rows.length === 0 ? (
        <Card>
          <Text style={styles.empty}>{empty}</Text>
        </Card>
      ) : view === 'sessions' ? (
        <Card>
          <View style={[styles.tableRow, styles.tableHead]}>
            <HeadCell title={L('회차', 'Session')} />
            <HeadCell title={L('음량 변화', 'Level change')} unit="(dB)" />
            <HeadCell title={L('말 속도', 'Rate')} unit={L('(음절/초)', '(syll/s)')} />
            <HeadCell title={L('무음 비율', 'Pause ratio')} unit="(%)" />
          </View>
          {rows.map((r) => (
            <View key={`${r.session}-${r.date}`} style={styles.tableRow}>
              <Text style={styles.cell}>{r.session}</Text>
              <Text style={styles.cell}>{show(r.levelChange, 1)}</Text>
              <Text style={styles.cell}>{show(r.rate, 2)}</Text>
              <Text style={styles.cell}>{show(r.pauseRatio, 0, '%')}</Text>
            </View>
          ))}
        </Card>
      ) : (
        <Card>
          <LineChart
            series={[{ label: L('음량 변화', 'Level change'), color: domains[0].color, values: series((r) => r.levelChange) }]}
            xLabels={xLabels}
          />
          <LineChart
            series={[{ label: L('말 속도', 'Speech rate'), color: domains[1].color, values: series((r) => r.rate) }]}
            xLabels={xLabels}
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
  demoBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    padding: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: '#FFF6DB',
  },
  demoText: {
    fontSize: 12,
    color: colors.text,
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
  empty: {
    fontSize: 13,
    color: colors.mutedText,
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
