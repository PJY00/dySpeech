// 끝나지 않은 회차(앱이 complete 전에 꺼짐). 측정 블록을 끝냈으면 보내기·버리기, 못 끝냈으면 버리기만.
import { StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import type { Session } from '@/session';
import { formatWhen, L, useLang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { unfinishedOptions } from '@/training/flow';
import { purposeLabel } from '@/training/labels';
import { useTraining } from '@/training/TrainingProvider';

export default function ResumeScreen() {
  useLang();
  const { unfinished, resolveUnfinished } = useTraining();

  return (
    <Screen
      header={<ScreenHeader title={L('끝나지 않은 회차', 'Unfinished sessions')} back={false} />}
      footer={
        unfinished.length === 0 ? <PrimaryButton label={L('홈으로', 'Go home')} chevron={false} onPress={() => router.dismissTo('/')} /> : undefined
      }
    >
      <Text style={styles.body}>
        {L(
          '앱이 중간에 꺼졌던 것 같아요. 회차마다 보낼지, 버릴지 골라 주세요.',
          'The app seems to have closed midway. Choose to send or discard each session.',
        )}
      </Text>
      {unfinished.map((s) => (
        <ResumeCard key={s.id} session={s} onSend={() => void resolveUnfinished(s, 'send')} onDiscard={() => void resolveUnfinished(s, 'discard')} />
      ))}
    </Screen>
  );
}

function ResumeCard({ session, onSend, onDiscard }: { session: Session; onSend: () => void; onDiscard: () => void }) {
  const when = new Date(session.startedAt);
  const options = unfinishedOptions(session);
  return (
    <Card>
      <Text style={styles.title}>
        {purposeLabel(session.purpose)} · {formatWhen(when)}
      </Text>
      <Text style={styles.note}>{L(`녹음 ${session.status().total}개`, `${session.status().total} recordings`)}</Text>
      {options.reason ? <Text style={styles.note}>{options.reason}</Text> : null}
      <View style={styles.actions}>
        {options.canSend ? <PrimaryButton label={L('지금까지 한 것 보내기', 'Send what was recorded')} chevron={false} onPress={onSend} /> : null}
        <PrimaryButton label={L('버리기', 'Discard')} chevron={false} onPress={onDiscard} />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  body: {
    fontSize: 14,
    color: colors.subText,
  },
  title: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  note: {
    fontSize: 12,
    color: colors.subText,
  },
  actions: {
    gap: spacing.sm,
  },
});
