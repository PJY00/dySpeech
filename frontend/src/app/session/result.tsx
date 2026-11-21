// 7. 훈련 결과 요약 화면. 이번 회차의 서버 지표와 진전도(기준선 대비 변화)를 보여 준다.
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';

import { Card } from '@/components/Card';
import { LineChart } from '@/components/LineChart';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { domains, type DomainId } from '@/data/domains';
import { openSession } from '@/session';
import type { SessionProgress } from '@/session';
import { L, useLang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { cardStatus, formatMeasurement, metricLabel } from '@/training/labels';
import { useTraining } from '@/training/TrainingProvider';
import { findMetric, percentSeries, useHistory } from '@/training/useHistory';

const RESULT_POLL_MS = 4000;

export default function SessionResultScreen() {
  useLang();
  const metricOptions = domains.map((d) => ({ value: d.id, label: metricLabel(d.metrics[0]) }));
  const { sessionId } = useLocalSearchParams<{ sessionId?: string }>();
  const { completed } = useTraining();
  const { history } = useHistory();
  const [progress, setProgress] = useState<SessionProgress | null>(null);
  const [checking, setChecking] = useState(false);
  const [area, setArea] = useState<DomainId>('breath');
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    const ac = new AbortController();
    abort.current = ac;
    void (async () => {
      const s = await openSession(sessionId);
      if (!s || ac.signal.aborted) return;
      const p = s.status();
      setProgress(p);
      const status = cardStatus({ state: p.state, lastErrorCode: p.lastError?.code ?? null });
      if (status !== 'sending' && status !== 'processing') return;
      setChecking(true);
      const done = await s.waitForResult({ intervalMs: RESULT_POLL_MS, signal: ac.signal, onProgress: (x) => !ac.signal.aborted && setProgress(x) });
      if (!ac.signal.aborted) {
        setProgress(done);
        setChecking(false);
      }
    })();
    return () => ac.abort();
  }, [sessionId]);

  const server = progress?.server;
  const domain = domains.find((d) => d.id === area) ?? domains[0];
  const trend = percentSeries(findMetric(history, domain.metrics[0]));
  const headline =
    progress?.state === 'done'
      ? L('오늘도 수고했어요!', 'Great work today!')
      : progress?.state === 'failed'
        ? L('서버 계산에 실패했어요', 'Server analysis failed')
        : progress?.state === 'blocked'
          ? L('보내기 중 오류가 났어요', 'An error occurred while sending')
          : L('결과를 기다리고 있어요', 'Waiting for your results');

  return (
    <Screen
      header={<ScreenHeader title={L('훈련 결과', 'Training results')} back={false} />}
      footer={
        <PrimaryButton
          label={L('확인', 'OK')}
          chevron={false}
          onPress={() => {
            abort.current?.abort();
            router.dismissTo('/');
          }}
        />
      }
    >
      <View style={styles.greeting}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={26} color={colors.primary} />
        </View>
        <View style={styles.greetingText}>
          <Text style={styles.hello}>{headline}</Text>
          <Text style={styles.sub}>
            {progress?.purpose === 'retest'
              ? L('반복 측정 회차예요.', 'This is a retest session.')
              : progress?.purpose === 'agc_test'
                ? L('폰 점검 회차예요. 결과는 서버 담당이 확인해요.', 'This is a phone check. The server team reviews the result.')
                : L(`오늘 완료한 영역 ${completed.length} / ${domains.length}`, `Areas done today ${completed.length} / ${domains.length}`)}
          </Text>
        </View>
        {checking ? <ActivityIndicator color={colors.primary} /> : null}
      </View>

      <Card>
        <Text style={styles.sectionTitle}>{L('이번 회차 지표', 'This session')}</Text>
        {server?.error ? <Text style={styles.warn}>
            {L('오류 코드', 'Error code')}: {server.error.code}
          </Text> : null}
        {progress?.lastError && progress.state !== 'done' ? (
          <Text style={styles.warn}>
            {L('보내기 오류', 'Send error')}: {progress.lastError.code}
          </Text>
        ) : null}
        {server && server.server_measurements.length > 0 ? (
          server.server_measurements.map((m) => (
            <View key={`${m.metric}-${m.algorithm_version}`} style={styles.metricRow}>
              <Text style={styles.metricLabel}>{metricLabel(m.metric)}</Text>
              <Text style={styles.metricValue}>{formatMeasurement(m)}</Text>
            </View>
          ))
        ) : (
          <Text style={styles.empty}>
            {progress && progress.total > 0 && progress.state !== 'done'
              ? L(
                  `녹음 올리기 ${progress.uploaded} / ${progress.total}. 앱을 닫아도 다음에 열 때 이어서 보내요.`,
                  `Uploading recordings ${progress.uploaded} / ${progress.total}. If you close the app, sending resumes next time.`,
                )
              : L('아직 서버 지표가 없어요.', 'No server metrics yet.')}
          </Text>
        )}
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>{L('지표 변화 (기준선 대비 %)', 'Change (% vs. baseline)')}</Text>
        <SegmentedTabs options={metricOptions} value={area} onChange={setArea} />
        {trend.length > 0 ? (
          <LineChart
            series={[{ label: metricLabel(domain.metrics[0]), color: domain.color, values: trend }]}
            xLabels={trend.length > 1 ? [L('1회차', 'S1'), L(`${trend.length}회차`, `S${trend.length}`)] : [L('1회차', 'S1')]}
            showLegend={false}
          />
        ) : (
          <Text style={styles.empty}>{L('아직 비교할 기록이 없어요.', 'Nothing to compare yet.')}</Text>
        )}
      </Card>
    </Screen>
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
  metricRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  metricLabel: {
    fontSize: 13,
    color: colors.subText,
  },
  metricValue: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
  },
  warn: {
    fontSize: 13,
    color: '#B3261E',
  },
  empty: {
    fontSize: 13,
    color: colors.mutedText,
  },
});
