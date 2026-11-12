// 폰 점검(자동 게인 조절 시험, api.md 9.3). 설정 탭에서 들어온다. 폰마다 한 번.
// 다른 기기 스피커로 시험 소리 3개를 하나씩 틀며 녹음하고, agc_test 회차로 보낸다. 판정은 서버가 한다.
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { MeasurementRecorder, prepareMicrophone } from '@/measurement';
import { discardRecordingFile, RecordingRejected, startSession, uuidv4 } from '@/session';
import type { AgcSignal, Session } from '@/session';
import { colors, radius, spacing } from '@/theme';

const SIGNALS: { signal: AgcSignal; title: string }[] = [
  { signal: 'agc_steps', title: '시험 소리 1' },
  { signal: 'agc_hold', title: '시험 소리 2' },
  { signal: 'agc_toggle', title: '시험 소리 3' },
];

const GUIDE = [
  '헤드셋을 폰에 꽂고, 헤드셋 마이크를 노트북 스피커 앞에 두세요. 끝날 때까지 옮기지 않아요.',
  '노트북 음량은 절반 정도로 두고 바꾸지 않아요. 이퀄라이저 같은 음향 효과는 꺼 주세요.',
  '녹음 시작을 누르고 1초 뒤에 시험 소리를 트세요. 소리가 끝나고 1초 뒤에 녹음을 멈추세요.',
];

type Stage = 'prepare' | 'ready' | 'recording' | 'saving' | 'sending';

export default function PhoneCheckScreen() {
  const [stage, setStage] = useState<Stage>('prepare');
  const [permission, setPermission] = useState<'checking' | 'granted' | 'denied'>('checking');
  const [index, setIndex] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const session = useRef<Session | null>(null);
  const recorder = useRef<MeasurementRecorder | null>(null);
  const recordingIds = useRef<string[]>([]);
  const sent = useRef(false);

  useEffect(() => {
    void (async () => {
      const p = await prepareMicrophone();
      setPermission(p);
      if (p !== 'granted') return;
      recorder.current = new MeasurementRecorder();
      session.current = await startSession('agc_test');
      setStage('ready');
    })().catch((e) => setError(String(e)));
    return () => {
      void (async () => {
        const left = await recorder.current?.release();
        if (left) await discardRecordingFile(left.fileUri);
        // 보내지 않고 나가면 이 회차는 버린다
        if (!sent.current) await session.current?.discard().catch(() => undefined);
      })();
    };
  }, []);

  async function start() {
    if (!recorder.current) return;
    setError(null);
    setElapsed(0);
    let t = 0;
    let n = 0;
    await recorder.current.start(uuidv4(), (_lvl, dt) => {
      t += dt;
      n += 1;
      if (n % 10 === 0) setElapsed(t);
    });
    setStage('recording');
  }

  async function stop() {
    if (!recorder.current || !session.current) return;
    setStage('saving');
    let fileUri: string | null = null;
    try {
      fileUri = (await recorder.current.stop()).fileUri;
      const id = await session.current.addRecording({ kind: 'agc_tone', agcSignal: SIGNALS[index].signal, fileUri });
      recordingIds.current[index] = id;
      setIndex(index + 1);
    } catch (e) {
      if (fileUri) await discardRecordingFile(fileUri).catch(() => undefined);
      setError(e instanceof RecordingRejected ? `녹음 파일 형식이 맞지 않아요: ${e.message}` : String(e));
    }
    setStage('ready');
  }

  async function redoLast() {
    const last = index - 1;
    const id = recordingIds.current[last];
    if (last < 0 || !id || !session.current) return;
    await session.current.removeRecording(id);
    setIndex(last);
  }

  async function send() {
    if (!session.current) return;
    setStage('sending');
    await session.current.complete();
    sent.current = true;
    router.replace({ pathname: '/session/result', params: { sessionId: session.current.id } });
  }

  const done = index >= SIGNALS.length;
  const current = SIGNALS[Math.min(index, SIGNALS.length - 1)];

  let footer = null;
  if (stage === 'ready' && !done) {
    footer = <PrimaryButton label={`${current.title} 녹음 시작`} chevron={false} onPress={() => void start()} />;
  } else if (stage === 'recording') {
    footer = <PrimaryButton label="녹음 멈춤" chevron={false} onPress={() => void stop()} />;
  } else if (stage === 'ready' && done) {
    footer = <PrimaryButton label="보내기" onPress={() => void send()} />;
  }

  return (
    <Screen header={<ScreenHeader title="폰 점검" />} footer={footer ?? undefined}>
      <Card>
        <Text style={styles.cardTitle}>폰마다 한 번, 5분 정도 걸려요</Text>
        <Text style={styles.body}>폰이 소리 크기를 스스로 바꾸는지 확인해요. 시험 소리 파일 3개와 노트북이 필요해요.</Text>
        {GUIDE.map((g, i) => (
          <View key={g} style={styles.guideRow}>
            <Text style={styles.guideNo}>{i + 1}</Text>
            <Text style={styles.guideText}>{g}</Text>
          </View>
        ))}
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>녹음</Text>
        {SIGNALS.map((s, i) => (
          <View key={s.signal} style={styles.signalRow}>
            <Ionicons
              name={i < index ? 'checkmark-circle' : i === index && stage === 'recording' ? 'radio-button-on' : 'ellipse-outline'}
              size={20}
              color={i < index ? colors.success : i === index && stage === 'recording' ? '#B3261E' : colors.mutedText}
            />
            <Text style={styles.signalText}>{s.title}</Text>
            {i === index && stage === 'recording' ? <Text style={styles.timer}>{elapsed.toFixed(1)}초</Text> : null}
          </View>
        ))}
        {index > 0 && stage === 'ready' ? (
          <Pressable onPress={() => void redoLast()} hitSlop={8}>
            <Text style={styles.redo}>{SIGNALS[index - 1].title} 다시 녹음</Text>
          </Pressable>
        ) : null}
      </Card>

      {permission === 'checking' || stage === 'saving' || stage === 'sending' ? <ActivityIndicator color={colors.primary} /> : null}
      {permission === 'denied' ? <Text style={styles.warn}>마이크 권한이 필요해요. 설정에서 마이크를 허용해 주세요.</Text> : null}
      {error ? <Text style={styles.warn}>{error}</Text> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  cardTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: colors.text },
  body: { fontSize: 14, lineHeight: 21, color: colors.subText },
  guideRow: { flexDirection: 'row', gap: spacing.sm },
  guideNo: {
    width: 22,
    height: 22,
    borderRadius: radius.pill,
    backgroundColor: colors.primaryLight,
    color: colors.primary,
    textAlign: 'center',
    fontWeight: '700',
    fontSize: 12,
    lineHeight: 22,
  },
  guideText: { flex: 1, fontSize: 14, lineHeight: 21, color: colors.text },
  signalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  signalText: { flex: 1, fontSize: 15, color: colors.text },
  timer: { fontSize: 15, fontWeight: '700', fontVariant: ['tabular-nums'], color: colors.text },
  redo: { fontSize: 14, fontWeight: '600', color: colors.subText, textAlign: 'center', paddingTop: spacing.sm },
  warn: { fontSize: 14, color: '#B3261E' },
});
