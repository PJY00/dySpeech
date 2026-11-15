import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { colors } from '@/theme';
import { TrainingProvider } from '@/training/TrainingProvider';

export default function RootLayout() {
  return (
    <TrainingProvider>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }} />
    </TrainingProvider>
  );
}
