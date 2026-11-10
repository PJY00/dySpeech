// 오늘 훈련 현황(N/3). 라운드 원래 값(성공률·판단)을 저장하고, 보여 줄 때마다 아래 규칙으로 계산한다.
// 규칙은 임시값이다. 기준을 바꾸면 저장된 오늘 기록도 새 기준으로 다시 계산된다.
// - 라운드 통과: 성공률 PASS_SUCCESS_RATE_PCT 이상(판단 up과 같은 기준). 가장 높은 단계는 hold가 나오므로 판단 대신 성공률로 본다
// - deferred(판정 미룸) 라운드는 통과로 세지 않는다
// - 영역 완료: 그날 그 영역 게임에서 통과한 라운드가 하나라도 있으면. 같은 영역은 하루 한 번만 센다

import type { Store } from '../session/store';
import type { Decision } from '../session/types';
import type { GameArea } from './gameTypes';

export const PASS_SUCCESS_RATE_PCT = 80;
export const TODAY_DOC = 'app/today-progress.json';

export interface RoundSummary {
  game: string;
  area: GameArea;
  success_rate_pct: number;
  decision: Decision;
}

interface TodayDoc {
  date: string;
  rounds: RoundSummary[];
}

/** 하루 단위 기록의 날짜 키(기기 현지 시간) */
export function dayKey(now: Date = new Date()): string {
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}

export function roundPassed(round: Pick<RoundSummary, 'success_rate_pct' | 'decision'>): boolean {
  return round.decision !== 'deferred' && round.success_rate_pct >= PASS_SUCCESS_RATE_PCT;
}

/** 오늘 완료한 영역(같은 영역은 한 번) */
export function completedAreas(rounds: RoundSummary[]): GameArea[] {
  const areas: GameArea[] = [];
  for (const r of rounds) {
    if (roundPassed(r) && !areas.includes(r.area)) areas.push(r.area);
  }
  return areas;
}

/** 오늘 라운드 기록을 기기에 둔다. 날짜가 바뀌면 빈 기록으로 본다 */
export class TodayBook {
  private readonly store: Store;
  private chain: Promise<unknown> = Promise.resolve();

  constructor(store: Store) {
    this.store = store;
  }

  async rounds(now = new Date()): Promise<RoundSummary[]> {
    const doc = (await this.store.readJson(TODAY_DOC)) as TodayDoc | null;
    return doc && doc.date === dayKey(now) ? doc.rounds : [];
  }

  add(round: RoundSummary, now = new Date()): Promise<RoundSummary[]> {
    return this.queue(async () => {
      const rounds = [...(await this.rounds(now)), round];
      await this.store.writeJson(TODAY_DOC, { date: dayKey(now), rounds } satisfies TodayDoc);
      return rounds;
    });
  }

  reset(now = new Date()): Promise<RoundSummary[]> {
    return this.queue(async () => {
      await this.store.writeJson(TODAY_DOC, { date: dayKey(now), rounds: [] } satisfies TodayDoc);
      return [];
    });
  }

  private queue<T>(job: () => Promise<T>): Promise<T> {
    const result = this.chain.then(job, job);
    this.chain = result.catch(() => undefined);
    return result;
  }
}
