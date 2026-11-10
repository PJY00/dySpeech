// 캘리브레이션 (빈 화면). 하루 첫 훈련에만 거친다.
import { router, useLocalSearchParams } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';

export default function CalibrationScreen() {
  const { next } = useLocalSearchParams<{ next?: string }>();

  return (
    <PlaceholderScreen
      title="캘리브레이션"
      description={'/아/ 지속 발성과 짧은 문장으로\n오늘의 기준값을 설정합니다.'}
      onNext={() => router.replace({ pathname: '/session/pre-test', params: { next: next ?? '/games' } })}
    />
  );
}
