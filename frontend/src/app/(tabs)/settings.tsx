// 설정 (시안 없음). 데모용 화면 진입 버튼을 모아 둔다.
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { colors, spacing } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';

export default function SettingsScreen() {
  const { completed, resetToday, startRetest } = useTraining();

  const resetTodayProgress = () => {
    void resetToday().then(() => Alert.alert('초기화했습니다', '오늘 완료 현황을 0/3으로 되돌렸습니다.'));
  };

  return (
    <Screen header={<ScreenHeader title="설정" back={false} />}>
      <Card>
        <Text style={styles.sectionTitle}>개발 · 데모</Text>
        <Row label="폰 점검 (자동 게인 조절 시험)" onPress={() => router.push('/phone-check')} />
        <Row label="반복 측정 시작 (MDC 수집용)" onPress={() => void startRetest()} />
        <Row label="실제 훈련 흐름 (app-shell)" onPress={() => router.push('/dev/shell')} />
        <Row label="첫 실행 화면 보기" onPress={() => router.push('/onboarding/profile')} />
        <Row label="임상가 화면 보기" onPress={() => router.push('/clinician')} />
        <Row label="오늘 완료 현황 초기화" detail={`${completed.length}/3`} onPress={resetTodayProgress} />
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
