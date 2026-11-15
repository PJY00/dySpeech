// 첫 실행: 프로필 등록 (빈 화면)
import { router } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';

export default function ProfileScreen() {
  return (
    <PlaceholderScreen
      title="프로필 등록"
      description={'이름, 성별, 나이를 입력합니다.\n처음 실행할 때 한 번만 진행합니다.'}
      onNext={() => router.push('/onboarding/consent')}
    />
  );
}
