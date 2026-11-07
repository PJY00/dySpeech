// 개발용 측정 블록 시험 화면: retest 세션으로 측정 블록 → complete → 서버 결과(done까지)를 확인한다.
// 앱 화면 구조(#18)에서 EXPO_PUBLIC_SCREEN 바로가기로 연다.

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import {
  apiConfig,
  currentUser,
  devUserIds,
  listSessions,
  resumePending,
  startAutoSync,
  startNewDevUser,
  startSession,
  syncNow,
} from '../session';
import type { Baselines, Session, SessionProgress, SessionSummary, UserInfo } from '../session';
import { MeasurementBlock } from './MeasurementBlock';

type View_ = 'home' | 'block' | 'result';

export function MeasurementTestScreen() {
  const [view, setView] = useState<View_>('home');
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [devIds, setDevIds] = useState<string[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [progress, setProgress] = useState<SessionProgress | null>(null);
  const [baselines, setBaselines] = useState<Baselines | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setUser(await currentUser());
    setDevIds(await devUserIds());
    setSessions((await listSessions()).slice(0, 10));
  }, []);

  useEffect(() => {
    void resumePending().then(refresh);
    return startAutoSync();
  }, [refresh]);

  async function begin() {
    const s = await startSession('retest', { clientMeta: { source: 'measurement_test' } });
    setSession(s);
    setProgress(null);
    setBaselines(null);
    setView('block');
  }

  async function finish(b: Baselines) {
    if (!session) return;
    setBaselines(b);
    setView('result');
    await session.complete({ clientMeta: { source: 'measurement_test' } });
    await waitResult(session);
  }

  async function waitResult(s: Session) {
    setBusy(true);
    try {
      setProgress(await s.waitForResult({ onProgress: setProgress }));
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  if (view === 'block' && session) {
    return (
      <MeasurementBlock
        session={session}
        onDone={(b) => void finish(b)}
        onCancel={() => {
          void session.discard().then(refresh);
          setView('home');
        }}
      />
    );
  }

  if (view === 'result' && session) {
    const server = progress?.server;
    const device = new Map(
      [
        ['noise_floor', baselines?.noiseFloorDbfs],
        ['sustained_level', baselines?.sustainedLevelDbfs],
        ['max_phonation_time', baselines?.maxPhonationTimeS],
        ['f0_median', baselines?.f0MedianHz],
      ].map(([k, v]) => [k as string, v as number | null | undefined]),
    );
    return (
      <Screen>
        <Text style={s.title}>반복 측정 시험 결과</Text>
        <Text style={s.mono}>세션 {session.id}</Text>
        <Text style={s.body}>기기 상태: {progress?.state ?? 'queued'}{busy ? ' (확인 중…)' : ''}</Text>
        <Text style={s.body}>
          올린 녹음: {progress?.uploaded ?? 0} / {progress?.total ?? 0}
        </Text>
        {server && <Text style={s.body}>서버 상태: {server.status}</Text>}
        {server?.error && <Text style={s.warn}>서버 오류: {server.error.code} {server.error.message}</Text>}
        {progress?.lastError && (
          <Text style={s.warn}>
            보내기 오류: {progress.lastError.code} {progress.lastError.message}
          </Text>
        )}
        {server?.server_measurements.map((m) => (
          <View key={m.metric} style={s.row}>
            <Text style={s.rowLabel}>{m.metric}</Text>
            <Text style={s.rowValue}>
              {m.value.toFixed(2)} {m.unit}
              {m.low_reliability ? ' (신뢰 낮음)' : ''}
              {device.get(m.metric) != null ? `  기기 ${(device.get(m.metric) as number).toFixed(2)}` : ''}
            </Text>
          </View>
        ))}
        <Button label="다시 확인" onPress={() => void waitResult(session)} />
        <Button label="처음으로" kind="secondary" onPress={() => setView('home')} />
      </Screen>
    );
  }

  return (
    <Screen>
      <Text style={s.title}>측정 블록 시험(개발용)</Text>
      <Text style={s.body}>서버: {apiConfig.url || '(EXPO_PUBLIC_API_URL 없음)'}</Text>
      <Text style={s.body}>앱 키: {apiConfig.hasKey ? '있음' : '(EXPO_PUBLIC_APP_KEY 없음)'}</Text>
      <Text style={s.mono}>
        사용자 {user?.userId ?? '…'} {user?.dev ? '(개발용)' : ''}
      </Text>
      <Button label="반복 측정 세션으로 시험 시작" onPress={() => void begin()} />
      <Button
        label="새 사용자로 시작(개발용)"
        kind="secondary"
        onPress={() => void startNewDevUser().then(refresh)}
      />
      <Button label="못 보낸 세션 지금 보내기" kind="secondary" onPress={() => void syncNow().then(refresh)} />
      {devIds.length > 0 && (
        <>
          <Text style={s.label}>개발용 사용자 ID(정리 대상)</Text>
          {devIds.map((id) => (
            <Text key={id} style={s.mono} selectable>
              {id}
            </Text>
          ))}
        </>
      )}
      <Text style={s.label}>최근 세션</Text>
      {sessions.map((x) => (
        <Text key={x.sessionId} style={s.mono} selectable>
          {x.startedAt.slice(0, 16)} {x.purpose} {x.state}
          {x.serverStatus ? `/${x.serverStatus}` : ''} {x.sessionId.slice(0, 8)}
        </Text>
      ))}
    </Screen>
  );
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      {children}
    </ScrollView>
  );
}

function Button({ label, onPress, kind = 'primary' }: { label: string; onPress: () => void; kind?: 'primary' | 'secondary' }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [kind === 'primary' ? s.primary : s.secondary, pressed && { opacity: 0.7 }]}>
      <Text style={kind === 'primary' ? s.primaryText : s.secondaryText}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#fff' },
  content: { padding: 20, paddingTop: 56, gap: 12 },
  title: { fontSize: 24, fontWeight: '700', color: '#111' },
  label: { fontSize: 16, fontWeight: '700', marginTop: 8, color: '#333' },
  body: { fontSize: 16, color: '#222' },
  warn: { fontSize: 15, color: '#b3261e' },
  mono: { fontSize: 12, fontFamily: 'monospace', color: '#444' },
  row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: '#eee', gap: 8 },
  rowLabel: { fontSize: 13, color: '#333', flexShrink: 1 },
  rowValue: { fontSize: 13, fontWeight: '600', color: '#111' },
  primary: { backgroundColor: '#1976d2', borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  secondary: { borderColor: '#1976d2', borderWidth: 2, borderRadius: 12, paddingVertical: 12, alignItems: 'center' },
  primaryText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  secondaryText: { color: '#1976d2', fontSize: 16, fontWeight: '600' },
});
