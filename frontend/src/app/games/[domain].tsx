// 게임 실행 화면 자리 (게임 1·2·3). 실제 게임은 아직 연결하지 않는다.
// 통과/실패는 임시 버튼으로 정하고, 통과하면 오늘 완료 현황에 기록한다.
import { router, useLocalSearchParams } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { findDomain } from '@/data/domains';
import { useTodayProgress } from '@/state/today-progress';

export default function GamePlaceholderScreen() {
  const { domain: domainId } = useLocalSearchParams<{ domain: string }>();
  const domain = findDomain(domainId);
  const { recordPass } = useTodayProgress();

  if (!domain) {
    return <PlaceholderScreen title="게임" description="알 수 없는 게임입니다." />;
  }

  const finish = (passed: boolean) => {
    if (passed) {
      recordPass(domain.id);
    }
    router.replace({ pathname: '/games/complete', params: { domain: domain.id, passed: String(passed) } });
  };

  return (
    <PlaceholderScreen
      title={`게임 ${domain.number} · ${domain.title}`}
      description={domain.games}
      nextLabel="통과로 완료 (임시)"
      onNext={() => finish(true)}
      secondaryLabel="실패로 완료 (임시)"
      onSecondary={() => finish(false)}
    />
  );
}
