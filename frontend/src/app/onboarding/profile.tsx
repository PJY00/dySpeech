// 첫 실행: 프로필 등록 (빈 화면)
import { router } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { L, useLang } from '@/i18n';

export default function ProfileScreen() {
  useLang();
  return (
    <PlaceholderScreen
      title={L('프로필 등록', 'Profile')}
      description={L('이름, 성별, 나이를 입력합니다.\n처음 실행할 때 한 번만 진행합니다.', 'Enter name, sex and age.\nOnly once, on first launch.')}
      onNext={() => router.push('/onboarding/consent')}
    />
  );
}
