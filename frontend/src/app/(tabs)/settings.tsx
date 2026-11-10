// 설정 (시안 없음). 데모용 화면 진입 버튼을 모아 둔다.
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { useDailyCheck } from '@/state/daily-check';
import { useTodayProgress } from '@/state/today-progress';
import { colors, spacing } from '@/theme';

export default function SettingsScreen() {
  const { isDoneToday, reset } = useDailyCheck();
  const { completedCount, totalCount, reset: resetProgress } = useTodayProgress();

  const resetTodayProgress = () => {
    resetProgress();
    Alert.alert('초기화했습니다', '오늘 완료 현황을 0/3으로 되돌렸습니다.');
  };

  const resetDailyCheck = () => {
    reset();
    Alert.alert('초기화했습니다', '다음 훈련 시작 시 캘리브레이션과 사전 측정을 다시 진행합니다.');
  };

  return (
    <Screen header={<ScreenHeader title="설정" back={false} />}>
      <Card>
        <Text style={styles.sectionTitle}>데모</Text>
        <Row label="실제 훈련 흐름 (app-shell)" onPress={() => router.push('/dev/shell')} />
        <Row label="첫 실행 화면 보기" onPress={() => router.push('/onboarding/profile')} />
        <Row label="임상가 화면 보기" onPress={() => router.push('/clinician')} />
        <Row
          label="오늘 측정 초기화"
          detail={isDoneToday ? '오늘 측정 완료' : '오늘 측정 전'}
          onPress={resetDailyCheck}
        />
        <Row
          label="오늘 완료 현황 초기화"
          detail={`${completedCount}/${totalCount}`}
          onPress={resetTodayProgress}
        />
      </Card>
    </Screen>
  );
}

function Row({ label, detail, onPress }: { label: string; detail?: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.row, pressed && styles.pressed]}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.right}>
        {detail ? <Text style={styles.detail}>{detail}</Text> : null}
        <Ionicons name="chevron-forward" size={18} color={colors.mutedText} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  sectionTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.subText,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  pressed: {
    opacity: 0.6,
  },
  label: {
    fontSize: 15,
    color: colors.text,
  },
  right: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  detail: {
    fontSize: 12,
    color: colors.subText,
  },
});
