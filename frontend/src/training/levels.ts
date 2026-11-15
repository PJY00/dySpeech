// 게임별 단계 저장(앱 쪽). 사용자마다, 게임마다 다음에 시작할 단계를 기기에 둔다.

import type { Store } from '../session/store';
import type { Decision } from '../session/types';

export const LEVELS_DOC = 'app/levels.json';

type LevelsDoc = Record<string, Record<string, { level: number; updatedAt: string }>>;

/** 게임 판단으로 다음 단계를 정한다: up +1, down −1, hold·deferred 그대로. 1~maxLevel 안 */
export function applyDecision(level: number, decision: Decision, maxLevel: number): number {
  const next = decision === 'up' ? level + 1 : decision === 'down' ? level - 1 : level;
  return Math.min(Math.max(1, maxLevel), Math.max(1, Math.round(next)));
}

export class LevelBook {
  private readonly store: Store;
  private chain: Promise<unknown> = Promise.resolve();

  constructor(store: Store) {
    this.store = store;
  }

  /** 저장된 단계. 없으면 1 */
  async get(userKey: string, game: string): Promise<number> {
    const doc = await this.read();
    return doc[userKey]?.[game]?.level ?? 1;
  }

  /** 라운드 결과를 반영해 다음 단계를 저장하고 돌려준다 */
  record(userKey: string, game: string, level: number, decision: Decision, maxLevel: number, now = new Date()): Promise<number> {
    const job = async () => {
      const doc = await this.read();
      const next = applyDecision(level, decision, maxLevel);
      doc[userKey] = { ...(doc[userKey] ?? {}), [game]: { level: next, updatedAt: now.toISOString() } };
      await this.store.writeJson(LEVELS_DOC, doc);
      return next;
    };
    const result = this.chain.then(job, job);
    this.chain = result.catch(() => undefined);
    return result;
  }

  private async read(): Promise<LevelsDoc> {
    return ((await this.store.readJson(LEVELS_DOC)) as LevelsDoc | null) ?? {};
  }
}
