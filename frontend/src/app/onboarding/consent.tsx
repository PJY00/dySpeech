// 첫 실행: 연구 활용 고지와 동의 (빈 화면)
import { router } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { L, useLang } from '@/i18n';

export default function ConsentScreen() {
  useLang();
  return (
    <PlaceholderScreen
      title={L('연구 활용 동의', 'Research consent')}
      description={L('녹음 보관 기간, 연구 활용, 동의 철회 방법을\n안내하고 동의를 받습니다.', 'Explains how long recordings are kept, research use,\nand how to withdraw consent.')}
      nextLabel={L('시작하기', 'Get started')}
      onNext={() => router.dismissTo('/')}
    />
  );
}
