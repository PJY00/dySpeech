// 2. 게임 선택 화면. 영역을 고르면 그 영역에 등록된 게임 목록으로 간다.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { DomainCard } from '@/components/DomainCard';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { domains } from '@/data/domains';
import { L, useLang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';

export default function GameSelectScreen() {
  useLang();
  const { session, roundsDone } = useTraining();

  return (
    <Screen header={<ScreenHeader title={L('게임 선택', 'Choose a game')} />}>
      {domains.map((domain) => (
        <DomainCard
          key={domain.id}
          domain={domain}
          size="large"
          onPress={() => router.push(`/games/${domain.id}`)}
        />
      ))}
      <View style={styles.hint}>
        <Text style={styles.hintText}>
          {L('게임을 선택하면\n훈련이 시작됩니다.', 'Pick a game\nto start training.')}
        </Text>
        <Text style={styles.rounds}>
          {L(`이번 회차에 한 라운드: ${roundsDone}개`, `Rounds this session: ${roundsDone}`)}
        </Text>
        {session ? (
          <Pressable onPress={() => router.replace('/session/finishing')} hitSlop={8}>
            <Text style={styles.finish}>{L('오늘 훈련 끝', 'Finish for today')}</Text>
          </Pressable>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  hint: {
    alignItems: 'center',
    gap: spacing.md,
    marginTop: spacing.xl,
  },
  hintText: {
    fontSize: 14,
    color: colors.subText,
    textAlign: 'center',
    lineHeight: 20,
  },
  rounds: {
    fontSize: 12,
    color: colors.mutedText,
  },
  finish: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.primary,
  },
});
