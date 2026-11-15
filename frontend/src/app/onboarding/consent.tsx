// 첫 실행: 연구 활용 고지와 동의 (빈 화면)
import { router } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';

export default function ConsentScreen() {
  return (
    <PlaceholderScreen
      title="연구 활용 동의"
      description={'녹음 보관 기간, 연구 활용, 동의 철회 방법을\n안내하고 동의를 받습니다.'}
      nextLabel="시작하기"
      onNext={() => router.dismissTo('/')}
    />
  );
}
