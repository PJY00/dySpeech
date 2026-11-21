// 설정 (시안 없음). 데모용 화면 진입 버튼을 모아 둔다.
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { SegmentedTabs } from '@/components/SegmentedTabs';
import { L, useLang } from '@/i18n';
import type { Lang } from '@/i18n';
import { colors, spacing } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';

export default function SettingsScreen() {
  const { completed, resetToday, startRetest } = useTraining();
  const { lang, setLang } = useLang();

  const resetTodayProgress = () => {
    void resetToday().then(() =>
      Alert.alert(L('초기화했습니다', 'Reset done'), L('오늘 완료 현황을 0/3으로 되돌렸습니다.', "Today's progress is back to 0/3.")),
    );
  };

  return (
    <Screen header={<ScreenHeader title={L('설정', 'Settings')} back={false} />}>
      <Card>
        <Text style={styles.sectionTitle}>{L('언어', 'Language')}</Text>
        <SegmentedTabs<Lang>
          options={[
            { value: 'ko', label: '한국어' },
            { value: 'en', label: 'English' },
          ]}
          value={lang}
          onChange={setLang}
        />
      </Card>
      <Card>
        <Text style={styles.sectionTitle}>{L('개발 · 데모', 'Development · Demo')}</Text>
        <Row label={L('폰 점검 (자동 게인 조절 시험)', 'Phone check (auto gain test)')} onPress={() => router.push('/phone-check')} />
        <Row label={L('반복 측정 시작 (MDC 수집용)', 'Start retest (for MDC)')} onPress={() => void startRetest()} />
        <Row label={L('실제 훈련 흐름 (app-shell)', 'Original training flow (app-shell)')} onPress={() => router.push('/dev/shell')} />
        <Row label={L('첫 실행 화면 보기', 'View first-launch screens')} onPress={() => router.push('/onboarding/profile')} />
        <Row label={L('임상가 화면 보기', 'View clinician screen')} onPress={() => router.push('/clinician')} />
        <Row label={L('오늘 완료 현황 초기화', "Reset today's progress")} detail={`${completed.length}/3`} onPress={resetTodayProgress} />
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
