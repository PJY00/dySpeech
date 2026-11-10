// 사전 측정 (빈 화면). 마치면 오늘 측정 완료로 표시하고 원래 가려던 화면으로 간다.
import { router, useLocalSearchParams, type Href } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { useDailyCheck } from '@/state/daily-check';

export default function PreTestScreen() {
  const { next } = useLocalSearchParams<{ next?: string }>();
  const { markDone } = useDailyCheck();

  const finish = () => {
    markDone();
    router.replace((next ?? '/games') as Href);
  };

  return (
    <PlaceholderScreen
      title="사전 측정"
      description={'훈련에 쓰지 않는 평가 문장을\n한 번 읽습니다.'}
      onNext={finish}
    />
  );
}
