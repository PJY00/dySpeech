import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { DailyCheckProvider } from '@/state/daily-check';
import { TodayProgressProvider } from '@/state/today-progress';
import { colors } from '@/theme';

export default function RootLayout() {
  return (
    <DailyCheckProvider>
      <TodayProgressProvider>
        <StatusBar style="dark" />
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }} />
      </TodayProgressProvider>
    </DailyCheckProvider>
  );
}
