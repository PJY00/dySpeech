// 캘리브레이션·사전 측정을 하루 한 번만 하도록 오늘 완료 여부를 관리한다.
// 지금은 메모리에만 두므로 앱을 완전히 종료하면 초기화된다. 저장 기능은 이후에 붙인다.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { router, type Href } from 'expo-router';

import { todayKey } from '@/state/today';

type DailyCheck = {
  isDoneToday: boolean;
  markDone: () => void;
  reset: () => void;
};

const DailyCheckContext = createContext<DailyCheck | null>(null);

export function DailyCheckProvider({ children }: { children: ReactNode }) {
  const [doneDate, setDoneDate] = useState<string | null>(null);

  const markDone = useCallback(() => setDoneDate(todayKey()), []);
  const reset = useCallback(() => setDoneDate(null), []);

  const value = useMemo(
    () => ({ isDoneToday: doneDate === todayKey(), markDone, reset }),
    [doneDate, markDone, reset],
  );

  return <DailyCheckContext.Provider value={value}>{children}</DailyCheckContext.Provider>;
}

export function useDailyCheck(): DailyCheck {
  const context = useContext(DailyCheckContext);
  if (!context) {
    throw new Error('useDailyCheck must be used inside DailyCheckProvider');
  }
  return context;
}

// 오늘 측정을 마쳤으면 목적지로 바로, 아니면 캘리브레이션 → 사전 측정을 거쳐 목적지로 간다.
export function useStartTraining() {
  const { isDoneToday } = useDailyCheck();

  return useCallback(
    (destination: string) => {
      if (isDoneToday) {
        router.push(destination as Href);
      } else {
        router.push({ pathname: '/session/calibration', params: { next: destination } });
      }
    },
    [isDoneToday],
  );
}
