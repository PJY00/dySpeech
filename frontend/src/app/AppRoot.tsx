// App.tsx가 띄우는 첫 화면. EXPO_PUBLIC_SCREEN(개발용 바로가기)에 따라 고른다(devScreen.ts).
// 바로가기는 개발 빌드(__DEV__)에서만 듣는다.

import { MeasurementTestScreen } from '../measurement';
import { AppShell } from './AppShell';
import { resolveDevScreen } from './devScreen';

export function AppRoot({ screen }: { screen?: string }) {
  const dev = resolveDevScreen(screen, __DEV__);
  if (!__DEV__ && screen?.trim()) console.log('[app] 릴리스 빌드라 EXPO_PUBLIC_SCREEN을 무시함', screen);
  if (dev.kind === 'measurement-test') return <MeasurementTestScreen />;
  if (dev.kind === 'game-select') return <AppShell dev={{ start: 'choose' }} />;
  if (dev.kind === 'game') return <AppShell dev={{ start: 'game', game: dev.game }} />;
  if (dev.kind === 'unknown') return <AppShell notice={`EXPO_PUBLIC_SCREEN=${dev.value}은 없는 바로가기라 기본 화면을 열었어요`} />;
  return <AppShell />;
}
