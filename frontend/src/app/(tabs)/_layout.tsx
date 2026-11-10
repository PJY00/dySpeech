import type { ComponentProps } from 'react';
import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router/js-tabs';

import { colors } from '@/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

// 탭 바가 넘겨주는 color는 RN 0.88 ColorValue라 아이콘 타입과 맞지 않아 focused로 색을 정한다.
function tabIcon(name: IconName) {
  return ({ focused, size }: { focused: boolean; size: number }) => (
    <Ionicons name={name} size={size} color={focused ? colors.primary : colors.mutedText} />
  );
}

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.mutedText,
      }}
    >
      <Tabs.Screen name="index" options={{ title: '홈', tabBarIcon: tabIcon('home') }} />
      <Tabs.Screen name="records" options={{ title: '기록', tabBarIcon: tabIcon('stats-chart') }} />
      <Tabs.Screen name="settings" options={{ title: '설정', tabBarIcon: tabIcon('settings-outline') }} />
    </Tabs>
  );
}
