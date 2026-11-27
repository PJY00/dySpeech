import type { ComponentProps } from 'react';
import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router/js-tabs';

import { L, useLang } from '@/i18n';
import { colors } from '@/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

// 탭 바가 넘겨주는 color는 RN 0.88 ColorValue라 아이콘 타입과 맞지 않아 focused로 색을 정한다.
function tabIcon(name: IconName) {
  return ({ focused, size }: { focused: boolean; size: number }) => (
    <Ionicons name={name} size={size} color={focused ? colors.primary : colors.mutedText} />
  );
}

export default function TabLayout() {
  useLang();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.mutedText,
      }}
    >
      <Tabs.Screen name="index" options={{ title: L('홈', 'Home'), tabBarIcon: tabIcon('home') }} />
      <Tabs.Screen name="records" options={{ title: L('기록', 'Records'), tabBarIcon: tabIcon('stats-chart') }} />
      <Tabs.Screen name="settings" options={{ title: L('설정', 'Settings'), tabBarIcon: tabIcon('settings-outline') }} />
    </Tabs>
  );
}
