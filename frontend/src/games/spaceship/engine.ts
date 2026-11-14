import type { PitchFrame } from './audio/PitchTracker';
import { clamp, hzToSt, median } from './audio/pitchMath';
import { LiveNoiseFloor, type SpaceshipBaseline } from './baseline';
import { deviationFrom, inGrace, phraseAt, shapeAt, type Round } from './contours';

export interface ScoredFrame {
  t: number;
  inPhrase: boolean;
  scored: boolean;
  targetSt: number | null;
  voiced: boolean;
  valid: boolean;
  st: number | null;
}

export interface GameState {
  t: number;
  shipSt: number;
  voiceSt: number | null;
  lastValidT: number;
  levelDbfs: number;
  noise: LiveNoiseFloor;
  offsetSt: number;
  warmupSts: number[];
  warmupDone: boolean;
  nextColumn: number;
  nextStar: number;
  columnHit: boolean[];
  starGot: boolean[];
  outside: boolean;
  hits: number;
  combo: number;
  bestCombo: number;
  flashUntil: number;
  frames: ScoredFrame[];
  done: boolean;
}

const HOLD_SEC = 0.15;
const STAR_WINDOW_SEC = 0.2;
const MAX_OFFSET_ST = 6;
const MIN_WARMUP_FRAMES = 30;

export function createGame(round: Round, baseline: SpaceshipBaseline): GameState {
  return {
    t: 0,
    shipSt: 0,
    voiceSt: null,
    lastValidT: -Infinity,
    levelDbfs: -Infinity,
    noise: new LiveNoiseFloor(baseline.noiseFloorDbfs),
    offsetSt: 0,
    warmupSts: [],
    warmupDone: false,
    nextColumn: 0,
    nextStar: 0,
    columnHit: new Array(round.columns.length).fill(false),
    starGot: new Array(round.stars.length).fill(false),
    outside: false,
    hits: 0,
    combo: 0,
    bestCombo: 0,
    flashUntil: -Infinity,
    frames: [],
    done: false,
  };
}

function warmupCenter(s: GameState): number {
  if (s.warmupDone || s.warmupSts.length < 5) return s.offsetSt;
  return clamp(median(s.warmupSts), -MAX_OFFSET_ST, MAX_OFFSET_ST);
}

export function referenceSt(s: GameState): number {
  return warmupCenter(s);
}

export function targetAt(s: GameState, round: Round, t: number): number | null {
  const p = phraseAt(round, t);
  if (!p) return null;
  if (p.warmup) return warmupCenter(s);
  return (shapeAt(round, t) as number) + s.offsetSt;
}

function finishWarmup(s: GameState): void {
  if (s.warmupDone) return;
  s.warmupDone = true;
  if (s.warmupSts.length >= MIN_WARMUP_FRAMES) {
    s.offsetSt = clamp(median(s.warmupSts), -MAX_OFFSET_ST, MAX_OFFSET_ST);
  }
}

export function addPitchFrame(
  s: GameState,
  round: Round,
  baseline: SpaceshipBaseline,
  f: PitchFrame,
  t: number,
): void {
  const phrase = phraseAt(round, t);
  const voiced = f.levelDbfs > s.noise.voicingThresholdDbfs;
  const valid = voiced && f.hz !== null;
  const st = valid ? hzToSt(f.hz as number, baseline.f0MedianHz) : null;
  s.levelDbfs = f.levelDbfs;
  if (!phrase) s.noise.addRestFrame(f.levelDbfs);
  if (phrase?.warmup && st !== null && !inGrace(round, t)) s.warmupSts.push(st);
  if (!s.warmupDone && phrase && !phrase.warmup) finishWarmup(s);
  if (st !== null) {
    s.voiceSt = st;
    s.lastValidT = t;
  } else if (t - s.lastValidT > HOLD_SEC) {
    s.voiceSt = null;
  }
  const targetSt = targetAt(s, round, t);
  const scored = phrase !== null && !phrase.warmup && !inGrace(round, t);
  s.frames.push({ t, inPhrase: phrase !== null, scored, targetSt, voiced, valid, st });
}

export function advance(s: GameState, round: Round, t: number): void {
  if (s.done) return;
  const dt = Math.max(0, t - s.t);
  s.t = t;
  if (!s.warmupDone) {
    const warm = round.phrases.find((p) => p.warmup);
    if (warm && t > warm.end) finishWarmup(s);
  }

  const singing = s.voiceSt !== null && t - s.lastValidT <= HOLD_SEC;
  const aim = singing ? (s.voiceSt as number) : s.offsetSt;
  const tau = singing ? 0.07 : 0.6;
  s.shipSt += (aim - s.shipSt) * (1 - Math.exp(-dt / tau));
  s.shipSt = clamp(s.shipSt, s.offsetSt - round.spanSt, s.offsetSt + round.spanSt);

  const tol = round.config.tolSt;
  const target = (time: number) => targetAt(s, round, time);
  while (s.nextColumn < round.columns.length && round.columns[s.nextColumn] <= t) {
    const tc = round.columns[s.nextColumn];
    const dev = inGrace(round, tc) ? null : deviationFrom(target, tc, s.shipSt);
    if (dev !== null && Math.abs(dev) > tol) {
      s.columnHit[s.nextColumn] = true;
      if (!s.outside) {
        s.hits++;
        s.flashUntil = t + 0.5;
      }
      s.outside = true;
      s.combo = 0;
    } else {
      s.outside = false;
      if (singing && dev !== null) {
        s.combo++;
        s.bestCombo = Math.max(s.bestCombo, s.combo);
      }
    }
    s.nextColumn++;
  }
  while (s.nextStar < round.stars.length && round.stars[s.nextStar] < t - STAR_WINDOW_SEC) s.nextStar++;
  const now = target(t);
  if (singing && now !== null && Math.abs(s.shipSt - now) <= tol) {
    for (let i = s.nextStar; i < round.stars.length && round.stars[i] <= t + STAR_WINDOW_SEC; i++) {
      s.starGot[i] = true;
    }
  }
  if (t >= round.duration) s.done = true;
}
