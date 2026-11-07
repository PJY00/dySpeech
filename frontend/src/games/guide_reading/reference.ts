// 기준 말속도와 음절 핵 검출 비율(사용자별, 기기에 저장).
// - 처음: 측정 블록 기준값(기기 조음속도)이 있으면 그 값, 없으면 기본값 4.0음절/초
// - 회차마다: 진전도의 articulation_rate 판정은 "다시 잡을지"만 정한다. increase·decrease면 그날 측정 블록의 기기 조음속도로
//   다시 잡고(게임 안 말속도도 기기 방식으로 재므로 같은 방식끼리 맞춤), no_change·pending_mdc(그 밖)면 유지한다.
//   그날 기기 조음속도가 없으면(null) 저장된 값을 유지한다
// - 지금은 최소 측정 가능 변화량이 없어 판정이 늘 pending_mdc이므로 기준 말속도가 첫 회차 값에 머문다
// - 검출 비율: 문장마다 검출 음절 수 ÷ 정답 음절 수를 지수 평균으로 쌓아, 진행 표시를 보정한다

import type { Store } from '../../session/store';
import type { History } from '../../session/types';
import { DEFAULT_REFERENCE_RATE } from './scoring';

export const STATE_DOC = 'games/guide_reading.json';
/** 검출 비율 처음 값. 실제 녹음 하나(빠른 낭독)에서 21음절 중 14개를 찾은 값(약 0.67)에 가깝게 둔다 */
export const DEFAULT_DETECTION_RATIO = 0.7;
const RATIO_WEIGHT = 0.3;
const RATIO_RANGE = { min: 0.3, max: 1.2 };
/** 기준 말속도가 벗어나지 않게 하는 범위(음절/초) */
const RATE_RANGE = { min: 1.5, max: 9 };

/** baseline: 첫 회차 측정 블록 값, default: 기본값, stored: 저장된 값 유지, rebaseline: 진전도 판정으로 그날 측정 블록 값으로 다시 잡음 */
export type ReferenceSource = 'baseline' | 'default' | 'stored' | 'rebaseline';

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

/** 진전도로 기준 말속도를 다시 잡을지: articulation_rate 판정이 increase·decrease면 참 */
export function shouldRebaseline(history: History | null): boolean {
  const m = history?.metrics.find((x) => x.metric === 'articulation_rate');
  return m?.status === 'increase' || m?.status === 'decrease';
}

/**
 * 라운드를 시작할 때 부른다. 저장된 상태를 읽고(없으면 만들고) 진전도 규칙을 반영해 저장한 뒤 돌려준다
 * todayRate: 그날 측정 블록의 기기 조음속도(음절/초, baselines.articulationRateSps). 없으면 null
 */
export async function loadReadingState(
  store: Store,
  userKey: string,
  todayRate: number | null,
  history: History | null,
  now = new Date(),
): Promise<ReadingState> {
  const doc = await readDoc(store);
  const stored = doc[userKey];
  let state: ReadingState = stored
    ? { ...stored, source: 'stored' }
    : {
        referenceRate: todayRate ?? DEFAULT_REFERENCE_RATE,
        source: todayRate !== null ? 'baseline' : 'default',
        detectionRatio: DEFAULT_DETECTION_RATIO,
        updatedAt: now.toISOString(),
      };
  if (stored && todayRate !== null && shouldRebaseline(history)) state = { ...state, referenceRate: todayRate, source: 'rebaseline' };
  state = { ...state, referenceRate: clamp(state.referenceRate, RATE_RANGE), updatedAt: now.toISOString() };
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
