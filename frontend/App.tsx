import { StatusBar } from 'expo-status-bar';
import { useState } from 'react';
import BalloonGameScreen from './src/games/balloon/BalloonGameScreen';
import TempCalibration from './src/shared/audio/TempCalibration';

// 임시: 게임 1 단독 테스트용. 앱 구조(네비게이션)가 머지되면 교체.
// 실제 앱: 앱 시작 시 baselineDb를 한 번 측정 → 모든 게임이 공유.
export default function App() {
  const [baselineDb, setBaselineDb] = useState<number | null>(null);

  return (
    <>
      {baselineDb === null ? (
        <TempCalibration onDone={setBaselineDb} />
      ) : (
        <BalloonGameScreen baselineDb={baselineDb} />
      )}
      <StatusBar style="auto" />
    </>
  );
}
