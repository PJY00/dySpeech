// 기준 말속도와 음절 핵 검출 비율(사용자별, 기기에 저장).
// - 기준 말속도 = 앱이 넘겨준 기준값(baselines.articulationRateSps). 오늘을 포함한 최근 3회차 측정 블록의
//   기기 조음속도 중앙값이다(training/gameTypes.ts의 gameBaselines). 다른 게임의 기준값과 같은 규칙이다
//   기기 값을 쓰는 것은 게임 안 말속도도 기기 방식으로 재기 때문이다(같은 방식끼리 맞춤)
// - 그 값이 없으면(최근 3회차에 조음속도가 하나도 없음) 저장된 값을 유지하고, 저장된 값도 없으면 기본값 4.0음절/초
// - 서버 진전도 판정은 쓰지 않는다. 라운드를 시작할 때 서버를 기다리지 않는다
// - 검출 비율: 문장마다 검출 음절 수 ÷ 정답 음절 수를 지수 평균으로 쌓아, 진행 표시를 보정한다

import type { Store } from '../../session/store';
import { DEFAULT_REFERENCE_RATE } from './scoring';

export const STATE_DOC = 'games/guide_reading.json';
/** 검출 비율 처음 값. 실제 녹음 하나(빠른 낭독)에서 21음절 중 14개를 찾은 값(약 0.67)에 가깝게 둔다 */
export const DEFAULT_DETECTION_RATIO = 0.7;
const RATIO_WEIGHT = 0.3;
const RATIO_RANGE = { min: 0.3, max: 1.2 };
/** 기준 말속도가 벗어나지 않게 하는 범위(음절/초) */
const RATE_RANGE = { min: 1.5, max: 9 };

/** recent_median: 최근 3회차 측정 블록 값의 중앙값, stored: 그 값이 없어 저장된 값 유지, default: 기본값 */
export type ReferenceSource = 'recent_median' | 'stored' | 'default';

export interface ReadingState {
  referenceRate: number;
  source: ReferenceSource;
  detectionRatio: number;
  updatedAt: string;
}

type Doc = Record<string, ReadingState>;

const clamp = (x: number, r: { min: number; max: number }) => Math.min(r.max, Math.max(r.min, x));

async function readDoc(store: Store): Promise<Doc> {
  return ((await store.readJson(STATE_DOC)) as Doc | null) ?? {};
}

/**
 * 라운드를 시작할 때 부른다. 기준 말속도를 정해 저장하고 돌려준다. 검출 비율은 저장된 값을 이어 쓴다
 * recentRate: 최근 3회차 측정 블록의 기기 조음속도 중앙값(음절/초, baselines.articulationRateSps). 없으면 null
 */
export async function loadReadingState(store: Store, userKey: string, recentRate: number | null, now = new Date()): Promise<ReadingState> {
  const doc = await readDoc(store);
  const stored = doc[userKey];
  const state: ReadingState = {
    referenceRate: clamp(recentRate ?? stored?.referenceRate ?? DEFAULT_REFERENCE_RATE, RATE_RANGE),
    source: recentRate !== null ? 'recent_median' : stored ? 'stored' : 'default',
    detectionRatio: stored?.detectionRatio ?? DEFAULT_DETECTION_RATIO,
    updatedAt: now.toISOString(),
  };
  doc[userKey] = state;
  await store.writeJson(STATE_DOC, doc);
  return state;
}

/** 문장 하나의 검출 결과로 검출 비율을 갱신해 저장한다: 새 비율 = 0.7 × 이전 + 0.3 × (검출 ÷ 정답) */
export async function recordDetection(store: Store, userKey: string, detected: number, expected: number, now = new Date()): Promise<number> {
  const doc = await readDoc(store);
  const prev = doc[userKey]?.detectionRatio ?? DEFAULT_DETECTION_RATIO;
  const ratio = expected > 0 ? clamp((1 - RATIO_WEIGHT) * prev + RATIO_WEIGHT * (detected / expected), RATIO_RANGE) : prev;
  const base = doc[userKey] ?? { referenceRate: DEFAULT_REFERENCE_RATE, source: 'default' as const, detectionRatio: prev, updatedAt: now.toISOString() };
  doc[userKey] = { ...base, detectionRatio: ratio, updatedAt: now.toISOString() };
  await store.writeJson(STATE_DOC, doc);
  return ratio;
}
