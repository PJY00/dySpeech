import { StatusBar } from 'expo-status-bar';

import { AppRoot } from './src/app/AppRoot';

// 앱 기본 화면. EXPO_PUBLIC_SCREEN은 개발용 바로가기(src/games/README.md)
export default function App() {
  return (
    <>
      <AppRoot screen={process.env.EXPO_PUBLIC_SCREEN} />
      <StatusBar style="auto" />
    </>
  );
}
