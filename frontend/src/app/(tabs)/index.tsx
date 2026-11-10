// 1. 기본 화면 (홈)
import { useEffect } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { DomainCard } from '@/components/DomainCard';
import { PrimaryButton } from '@/components/PrimaryButton';
import { ProgressRing } from '@/components/ProgressRing';
import { Screen } from '@/components/Screen';
import { domains } from '@/data/domains';
import { todaySummary } from '@/data/mock';
import { useStartTraining } from '@/state/daily-check';
import { useTodayProgress } from '@/state/today-progress';
import { colors, spacing } from '@/theme';

// 개발용 바로가기(EXPO_PUBLIC_SCREEN)가 있으면 앱을 처음 열 때 한 번만 app-shell 흐름으로 보낸다.
let devShortcutOpened = false;

export default function HomeScreen() {
  const startTraining = useStartTraining();
  const { completedCount, totalCount } = useTodayProgress();

  useEffect(() => {
    if (__DEV__ && !devShortcutOpened && process.env.EXPO_PUBLIC_SCREEN?.trim()) {
      devShortcutOpened = true;
      router.push('/dev/shell');
    }
  }, []);

  return (
    <Screen
      header={
        <View style={styles.topBar}>
          <Text style={styles.logoText}>dySpeech</Text>
          <Pressable onPress={() => router.navigate('/settings')} hitSlop={12} accessibilityLabel="설정">
            <Ionicons name="settings-outline" size={22} color={colors.text} />
          </Pressable>
        </View>
      }
    >
      <View style={styles.greeting}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={28} color={colors.primary} />
        </View>
        <View style={styles.greetingText}>
          <Text style={styles.hello}>안녕하세요!</Text>
          <Text style={styles.hello}>오늘도 멋진 하루예요</Text>
          <Text style={styles.sub}>지금 바로 훈련을 시작해볼까요?</Text>
        </View>
      </View>

      <Card>
        <View style={styles.rowBetween}>
          <Text style={styles.sectionTitle}>오늘의 훈련 현황</Text>
          <Pressable onPress={() => router.navigate('/records')} hitSlop={8}>
            <Text style={styles.link}>상세보기 ›</Text>
          </Pressable>
        </View>
        <View style={styles.summary}>
          <ProgressRing done={completedCount} total={totalCount} />
          <Metric label="평균 음량" value={`${todaySummary.averageDb} dB`} />
          <Metric label="평균 발화 속도" value={`${todaySummary.averageRate}`} />
          <Metric label="평균 F0" value={`${todaySummary.averageF0} Hz`} />
        </View>
      </Card>

      <PrimaryButton label="훈련 시작하기" onPress={() => startTraining('/games')} />

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>게임 선택하기</Text>
        {domains.map((domain) => (
          <DomainCard key={domain.id} domain={domain} onPress={() => startTraining(`/games/${domain.id}`)} />
        ))}
      </View>
    </Screen>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  topBar: {
    height: 52,
    paddingHorizontal: spacing.lg,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  logoText: {
    fontSize: 20,
    fontWeight: '800',
    color: colors.text,
  },
  greeting: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  avatar: {
    width: 60,
    height: 60,
    borderRadius: 30,
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
    marginTop: 2,
    fontSize: 13,
    color: colors.subText,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  link: {
    fontSize: 12,
    color: colors.subText,
  },
  summary: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  metric: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
  },
  metricLabel: {
    fontSize: 11,
    color: colors.subText,
  },
  metricValue: {
    fontSize: 16,
    fontWeight: '800',
    color: colors.text,
  },
  section: {
    gap: spacing.sm,
  },
});
