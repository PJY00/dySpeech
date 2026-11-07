import type { PitchFrame } from './audio/PitchTracker';
import { clamp, hzToSt } from './audio/pitchMath';
import { LiveNoiseFloor, type SpaceshipBaseline } from './baseline';
import { deviationAt, targetStAt, type Round } from './contours';

export interface ScoredFrame {
  t: number;
  inPhrase: boolean;
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
  nextColumn: number;
  nextStar: number;
  columnHit: boolean[];
  starGot: boolean[];
  hits: number;
  combo: number;
  bestCombo: number;
  flashUntil: number;
  frames: ScoredFrame[];
  done: boolean;
}

const HOLD_SEC = 0.15;

export function createGame(round: Round, baseline: SpaceshipBaseline): GameState {
  return {
    t: 0,
    shipSt: 0,
    voiceSt: null,
    lastValidT: -Infinity,
    levelDbfs: -Infinity,
    noise: new LiveNoiseFloor(baseline.noiseFloorDbfs),
    nextColumn: 0,
    nextStar: 0,
    columnHit: new Array(round.columns.length).fill(false),
    starGot: new Array(round.stars.length).fill(false),
    hits: 0,
    combo: 0,
    bestCombo: 0,
    flashUntil: -Infinity,
    frames: [],
    done: false,
  };
}

export function addPitchFrame(
  s: GameState,
  round: Round,
  baseline: SpaceshipBaseline,
  f: PitchFrame,
  t: number,
): void {
  const targetSt = targetStAt(round, t);
  const voiced = f.levelDbfs > s.noise.voicingThresholdDbfs;
  const valid = voiced && f.hz !== null;
  const st = valid ? hzToSt(f.hz as number, baseline.f0MedianHz) : null;
  s.levelDbfs = f.levelDbfs;
  if (targetSt === null) s.noise.addRestFrame(f.levelDbfs);
  if (st !== null) {
    s.voiceSt = st;
    s.lastValidT = t;
  } else if (t - s.lastValidT > HOLD_SEC) {
    s.voiceSt = null;
  }
  s.frames.push({ t, inPhrase: targetSt !== null, targetSt, voiced, valid, st });
}

export function advance(s: GameState, round: Round, t: number): void {
  if (s.done) return;
  const dt = Math.max(0, t - s.t);
  s.t = t;

  const singing = s.voiceSt !== null && t - s.lastValidT <= HOLD_SEC;
  const aim = singing ? (s.voiceSt as number) : 0;
  const tau = singing ? 0.07 : 0.6;
  s.shipSt += (aim - s.shipSt) * (1 - Math.exp(-dt / tau));
  s.shipSt = clamp(s.shipSt, -round.spanSt, round.spanSt);

  const tol = round.config.tolSt;
  while (s.nextColumn < round.columns.length && round.columns[s.nextColumn] <= t) {
    const dev = deviationAt(round, round.columns[s.nextColumn], s.shipSt);
    if (dev !== null && Math.abs(dev) > tol) {
      s.columnHit[s.nextColumn] = true;
      s.hits++;
      s.combo = 0;
      s.flashUntil = t + 0.5;
    } else if (singing) {
      s.combo++;
      s.bestCombo = Math.max(s.bestCombo, s.combo);
    }
    s.nextColumn++;
  }
  while (s.nextStar < round.stars.length && round.stars[s.nextStar] <= t) {
    const dev = deviationAt(round, round.stars[s.nextStar], s.shipSt);
    if (singing && dev !== null && Math.abs(dev) <= tol) s.starGot[s.nextStar] = true;
    s.nextStar++;
  }
  if (t >= round.duration) s.done = true;
}

export function liveAccuracy(s: GameState, round: Round): number | null {
  let n = 0;
  let ok = 0;
  for (const f of s.frames) {
    if (!f.inPhrase || f.targetSt === null) continue;
    n++;
    const dev = f.st === null ? null : deviationAt(round, f.t, f.st);
    if (dev !== null && Math.abs(dev) <= round.config.tolSt) ok++;
  }
  return n ? ok / n : null;
}
