// 측정은 하루 한 번(#25 토의 2, A+B). 회차를 하루 하나로 묶는다.
// - A: 오늘 측정을 마친 훈련 회차는 앱을 다시 열어도 이어서 한다. 지난 날 회차는 다음 날 열 때 자동으로 끝내고 보낸다
//      끝난 시각(ended_at)은 앱을 연 시각이 아니라 마지막 라운드가 끝난 시각(라운드가 없으면 측정이 끝난 시각)
// - B: [오늘 훈련 끝] 뒤 그날은 새 훈련 회차를 열지 않는다(reportedToday)
// 열린 회차의 라운드 수와 마지막 활동 시각은 기기에 둔다(세션 모듈 밖의 앱 상태).

import type { Store } from '../session/store';
import type { Baselines, Purpose, SessionSummary } from '../session/types';
import { dayKey } from './progress';

export const OPEN_SESSION_DOC = 'app/open-session.json';

export interface OpenSessionDoc {
  sessionId: string;
  /** 게임별로 끝낸 라운드 수 */
  rounds: Record<string, number>;
  /** 마지막 라운드가 끝난 시각(라운드가 없으면 측정이 끝난 시각) */
  lastActivityAt: string;
}

/** 앱 시작 때 complete 전 회차를 나누는 데 필요한 것만 */
export interface UnfinishedLike {
  id: string;
  purpose: Purpose;
  startedAt: string;
  baselines(): Baselines | null;
}

export interface UnfinishedPlan<S extends UnfinishedLike> {
  /** 오늘 측정을 마친 훈련 회차: 이어서 한다 */
  reopen: S | null;
  /** 지난 날 측정을 마친 훈련 회차: 끝난 시각을 정해 자동으로 끝낸다 */
  close: { session: S; endedAt: string }[];
  /** 나머지(측정을 못 끝냄, 반복 측정 등): 사용자에게 보내기·버리기를 묻는다 */
  ask: S[];
}

export function planUnfinished<S extends UnfinishedLike>(sessions: S[], doc: OpenSessionDoc | null, now = new Date()): UnfinishedPlan<S> {
  const plan: UnfinishedPlan<S> = { reopen: null, close: [], ask: [] };
  const newestFirst = [...sessions].sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  for (const s of newestFirst) {
    const measured = s.baselines();
    if (s.purpose !== 'training' || !measured) {
      plan.ask.push(s);
    } else if (dayKey(new Date(s.startedAt)) === dayKey(now) && !plan.reopen) {
      plan.reopen = s;
    } else {
      plan.close.push({ session: s, endedAt: doc?.sessionId === s.id ? doc.lastActivityAt : measured.measuredAt });
    }
  }
  return plan;
}

/** 오늘 이미 끝낸(complete한) 훈련 회차가 있는지 */
export function reportedToday(list: Pick<SessionSummary, 'purpose' | 'state' | 'startedAt'>[], now = new Date()): boolean {
  return list.some((x) => x.purpose === 'training' && x.state !== 'recording' && dayKey(new Date(x.startedAt)) === dayKey(now));
}

export class OpenSessionBook {
  private readonly store: Store;

  constructor(store: Store) {
    this.store = store;
  }

  async read(): Promise<OpenSessionDoc | null> {
    return (await this.store.readJson(OPEN_SESSION_DOC)) as OpenSessionDoc | null;
  }

  write(doc: OpenSessionDoc | null): Promise<void> {
    return this.store.writeJson(OPEN_SESSION_DOC, doc);
  }
}
