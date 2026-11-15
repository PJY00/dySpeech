// 개발용: app-shell의 실제 훈련 흐름(측정 블록 → 게임 → 서버로 보내기 → 결과)을 그대로 띄운다.
// 시안 화면에 기능을 옮겨 붙이는 동안 테스트를 마친 흐름을 확인하는 용도. 옮기기가 끝나면 지운다.
// EXPO_PUBLIC_SCREEN 바로가기(src/games/README.md)도 이 화면이 처리한다.
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppRoot } from '@/training/AppRoot';
import { colors } from '@/theme';

export default function DevShellScreen() {
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}>
      <AppRoot screen={process.env.EXPO_PUBLIC_SCREEN} />
    </SafeAreaView>
  );
}
