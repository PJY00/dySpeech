// 측정 블록: (첫 회차) 캘리브레이션 → 조용히 → "아" 2~3회 → 평가 문장. 시안의 캘리브레이션·사전 측정 자리.
// 끝나면 기준값을 저장하고 원래 가려던 화면(next)으로 간다.
import { router, useLocalSearchParams, type Href } from 'expo-router';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { MeasurementBlock } from '@/measurement';
import { useTraining } from '@/training/TrainingProvider';

export default function MeasureScreen() {
  const { next } = useLocalSearchParams<{ next?: string }>();
  const { session, onMeasured, cancelMeasure } = useTraining();

  if (!session) {
    return <PlaceholderScreen title="측정" description="열린 회차가 없어요. 홈에서 다시 시작해 주세요." />;
  }

  return (
    <MeasurementBlock
      session={session}
      onDone={(baselines) => {
        onMeasured(baselines);
        router.replace((next ?? '/games') as Href);
      }}
      onCancel={() => {
        void cancelMeasure();
        router.back();
      }}
    />
  );
}
