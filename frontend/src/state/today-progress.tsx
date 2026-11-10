// 오늘 통과한 게임 영역을 기록해 홈의 N/3 완료 현황을 만든다.
// 영역(호흡·속도·억양) 단위로 세고, 같은 영역은 하루 한 번만 센다. 통과한 경우만 기록한다.
// 지금은 메모리에만 두므로 앱을 완전히 종료하면 초기화된다.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { domains, type DomainId } from '@/data/domains';
import { todayKey } from '@/state/today';

type TodayProgress = {
  passedDomains: DomainId[];
  completedCount: number;
  totalCount: number;
  recordPass: (domain: DomainId) => void;
  reset: () => void;
};

const TodayProgressContext = createContext<TodayProgress | null>(null);

export function TodayProgressProvider({ children }: { children: ReactNode }) {
  const [progress, setProgress] = useState<{ date: string; domains: DomainId[] }>({
    date: todayKey(),
    domains: [],
  });

  const recordPass = useCallback((domain: DomainId) => {
    setProgress((current) => {
      const today = todayKey();
      const passed = current.date === today ? current.domains : [];
      return passed.includes(domain) ? current : { date: today, domains: [...passed, domain] };
    });
  }, []);

  const reset = useCallback(() => setProgress({ date: todayKey(), domains: [] }), []);

  const value = useMemo(() => {
    const passedDomains = progress.date === todayKey() ? progress.domains : [];
    return {
      passedDomains,
      completedCount: passedDomains.length,
      totalCount: domains.length,
      recordPass,
      reset,
    };
  }, [progress, recordPass, reset]);

  return <TodayProgressContext.Provider value={value}>{children}</TodayProgressContext.Provider>;
}

export function useTodayProgress(): TodayProgress {
  const context = useContext(TodayProgressContext);
  if (!context) {
    throw new Error('useTodayProgress must be used inside TodayProgressProvider');
  }
  return context;
}
