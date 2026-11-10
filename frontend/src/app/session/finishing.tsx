// 오늘 훈련 끝·반복 측정 끝: 회차를 complete하고, 보내기와 서버 계산이 끝날 때까지 기다린다.
// 메인으로 가면 이 화면의 기다리기만 멈추고 보내기는 세션 모듈이 계속한다.
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import type { SessionProgress } from '@/session';
import { colors, spacing } from '@/theme';
import { STATE_LABELS } from '@/training/labels';
import { useTraining } from '@/training/TrainingProvider';

const RESULT_POLL_MS = 4000;

export default function FinishingScreen() {
  const { finish } = useTraining();
  const [progress, setProgress] = useState<SessionProgress | null>(null);
  const started = useRef(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const ac = new AbortController();
    abort.current = ac;
    void (async () => {
      const s = await finish();
      if (!s) {
        router.dismissTo('/');
        return;
      }
      setProgress(s.status());
      if (ac.signal.aborted) return;
      await s.waitForResult({ intervalMs: RESULT_POLL_MS, signal: ac.signal, onProgress: (p) => !ac.signal.aborted && setProgress(p) });
      if (!ac.signal.aborted) router.replace({ pathname: '/session/result', params: { sessionId: s.id } });
    })();
    return () => ac.abort();
  }, [finish]);

  const goHome = () => {
    abort.current?.abort();
    router.dismissTo('/');
  };

  return (
    <Screen
      scroll={false}
      header={<ScreenHeader title="결과 기다리기" back={false} />}
      footer={<PrimaryButton label="메인으로" chevron={false} onPress={goHome} />}
    >
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={styles.title}>결과를 기다리고 있어요</Text>
        <Text style={styles.state}>{progress ? STATE_LABELS[progress.state] : '저장하는 중'}</Text>
        {progress && progress.total > 0 ? (
          <Text style={styles.note}>
            녹음 올리기 {progress.uploaded} / {progress.total}
          </Text>
        ) : null}
        <Text style={styles.note}>앱을 닫아도 다음에 열 때 이어서 보내요.</Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.md,
  },
  title: {
    fontSize: 20,
    fontWeight: '800',
    color: colors.text,
  },
  state: {
    fontSize: 15,
    color: colors.subText,
  },
  note: {
    fontSize: 13,
    color: colors.mutedText,
  },
});
