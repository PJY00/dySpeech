export type PatternId = 'level' | 'rise' | 'fall' | 'riseFall' | 'fallRise' | 'stepUp';

type Key = readonly [u: number, v: number];

export const PATTERNS: Record<PatternId, { label: string; hint: string; keys: readonly Key[] }> = {
  level: { label: '평평하게', hint: '편한 높이로 길게 "아—"', keys: [[0, 0], [1, 0]] },
  rise: { label: '올리기 ↗', hint: '물어볼 때처럼 끝을 올려요', keys: [[0, -0.3], [0.35, -0.3], [1, 0.7]] },
  fall: { label: '내리기 ↘', hint: '말을 마칠 때처럼 끝을 내려요', keys: [[0, 0.5], [0.35, 0.5], [1, -0.5]] },
  riseFall: { label: '올렸다 내리기', hint: '가운데를 높게', keys: [[0, -0.3], [0.5, 0.7], [1, -0.3]] },
  fallRise: { label: '내렸다 올리기', hint: '가운데를 낮게', keys: [[0, 0.5], [0.5, -0.5], [1, 0.5]] },
  stepUp: { label: '한 계단 올리기', hint: '중간에 한 번 높여요', keys: [[0, -0.3], [0.4, -0.3], [0.6, 0.7], [1, 0.7]] },
};

export function patternValue(id: PatternId, u: number): number {
  const keys = PATTERNS[id].keys;
  const x = Math.min(1, Math.max(0, u));
  for (let i = 1; i < keys.length; i++) {
    const [u0, v0] = keys[i - 1];
    const [u1, v1] = keys[i];
    if (x <= u1) {
      const s = u1 === u0 ? 1 : (x - u0) / (u1 - u0);
      return v0 + (v1 - v0) * (1 - Math.cos(Math.PI * s)) / 2;
    }
  }
  return keys[keys.length - 1][1];
}

export interface LevelConfig {
  level: number;
  ampSt: number;
  tolSt: number;
  pxPerSec: number;
  phraseSec: number;
  restSec: number;
  patterns: PatternId[];
}

export const LEVELS: readonly LevelConfig[] = [
  { level: 1, ampSt: 3, tolSt: 3.0, pxPerSec: 80, phraseSec: 4.0, restSec: 2.5, patterns: ['rise', 'fall'] },
  { level: 2, ampSt: 4, tolSt: 2.6, pxPerSec: 90, phraseSec: 3.6, restSec: 2.3, patterns: ['rise', 'fall', 'stepUp'] },
  { level: 3, ampSt: 5, tolSt: 2.3, pxPerSec: 100, phraseSec: 3.2, restSec: 2.0, patterns: ['rise', 'fall', 'riseFall', 'stepUp'] },
  { level: 4, ampSt: 6, tolSt: 2.0, pxPerSec: 110, phraseSec: 3.0, restSec: 1.8, patterns: ['rise', 'fall', 'riseFall', 'fallRise', 'stepUp'] },
  { level: 5, ampSt: 7, tolSt: 1.8, pxPerSec: 120, phraseSec: 2.8, restSec: 1.6, patterns: ['rise', 'fall', 'riseFall', 'fallRise', 'stepUp'] },
];

export const MAX_LEVEL = LEVELS.length;

export function getLevel(level: number): LevelConfig {
  return LEVELS[Math.min(MAX_LEVEL, Math.max(1, Math.round(level))) - 1];
}

export interface Phrase {
  index: number;
  pattern: PatternId;
  start: number;
  end: number;
  warmup: boolean;
}

export interface Round {
  config: LevelConfig;
  phrases: Phrase[];
  leadIn: number;
  duration: number;
  spanSt: number;
  columns: number[];
  stars: number[];
  columnStep: number;
  seed: number;
}

export const PHRASE_COUNT = 6;
export const COLUMN_STEP_SEC = 0.2;
export const ONSET_GRACE_SEC = 0.5;
export const REACTION_ALLOWANCE_SEC = 0.2;
const STAR_STEP_SEC = 0.6;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildRound(level: number, phraseCount = PHRASE_COUNT, seed = Date.now() % 2147483647): Round {
  const config = getLevel(level);
  const rand = mulberry32(seed);
  const leadIn = 3;
  const phrases: Phrase[] = [];
  let t = leadIn;
  let prev: PatternId = 'level';
  for (let i = 0; i < phraseCount; i++) {
    let pattern: PatternId = 'level';
    if (i > 0) {
      const pool = config.patterns.filter((p) => p !== prev);
      pattern = pool[Math.floor(rand() * pool.length)] ?? config.patterns[0];
    }
    phrases.push({ index: i, pattern, start: t, end: t + config.phraseSec, warmup: i === 0 });
    prev = pattern;
    t += config.phraseSec + config.restSec;
  }
  const last = phrases[phrases.length - 1];
  const columns: number[] = [];
  const stars: number[] = [];
  for (const p of phrases) {
    for (let c = p.start; c <= p.end + 1e-9; c += COLUMN_STEP_SEC) columns.push(c);
    for (let s = p.start + STAR_STEP_SEC / 2; s < p.end; s += STAR_STEP_SEC) stars.push(s);
  }
  return {
    config,
    phrases,
    leadIn,
    duration: last.end + 1.5,
    spanSt: Math.max(6, 0.7 * config.ampSt + config.tolSt + 2),
    columns,
    stars,
    columnStep: COLUMN_STEP_SEC,
    seed,
  };
}

export function phraseAt(round: Round, t: number): Phrase | null {
  for (const p of round.phrases) if (t >= p.start && t <= p.end) return p;
  return null;
}

export function nextPhrase(round: Round, t: number): Phrase | null {
  for (const p of round.phrases) if (p.start > t) return p;
  return null;
}

export function inGrace(round: Round, t: number): boolean {
  const p = phraseAt(round, t);
  return p !== null && t - p.start < ONSET_GRACE_SEC;
}

export function shapeAt(round: Round, t: number): number | null {
  const p = phraseAt(round, t);
  if (!p) return null;
  return patternValue(p.pattern, (t - p.start) / (p.end - p.start)) * round.config.ampSt;
}

export function deviationFrom(target: (t: number) => number | null, t: number, st: number): number | null {
  if (target(t) === null) return null;
  let best: number | null = null;
  for (let k = 0; k <= 4; k++) {
    const v = target(t - (REACTION_ALLOWANCE_SEC * k) / 4);
    if (v === null) continue;
    const d = st - v;
    if (best === null || Math.abs(d) < Math.abs(best)) best = d;
  }
  return best;
}
