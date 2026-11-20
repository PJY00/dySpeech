import { phraseAt, shapeAt, type Round } from '../spaceship/contours';

const Z0 = 0.5;
const EYE = 1;
const GAUGE = 0.34;
const BED = 0.5;
const TIE_STEP_SEC = 0.2;
const TIE_DEPTH_SEC = 0.035;
const PILLAR_STEP_SEC = 0.5;
const PILLAR_W = 0.05;
const COIN_UP = 0.28;
const COIN_R = 0.11;
const SAMPLES = 60;

export interface CoinSprite { i: number; x: number; y: number; r: number; }

export interface Scene {
  horizonY: number;
  roll: number;
  speed: number;
  noseY: number;
  railNearY: number;
  bed: string;
  ties: string;
  tiesActive: string;
  tiesUp: string;
  tiesDown: string;
  rails: string;
  pillars: string;
  coins: CoinSprite[];
}

function phraseValue(round: Round, t: number): number {
  const p = phraseAt(round, t);
  if (!p || p.warmup) return 0;
  return shapeAt(round, t) ?? 0;
}

export function trackSt(round: Round, t: number): number {
  if (phraseAt(round, t)) return phraseValue(round, t);
  let prevEnd = -Infinity;
  let from = 0;
  let nextStart = Infinity;
  let to = 0;
  for (const p of round.phrases) {
    if (p.end < t && p.end > prevEnd) {
      prevEnd = p.end;
      from = phraseValue(round, p.end);
    }
    if (p.start > t && p.start < nextStart) {
      nextStart = p.start;
      to = phraseValue(round, p.start);
    }
  }
  if (prevEnd === -Infinity) return to;
  if (nextStart === Infinity) {
    const u = Math.min(1, (t - prevEnd) / 1.2);
    return from * (1 - u * u * (3 - 2 * u));
  }
  const u = (t - prevEnd) / (nextStart - prevEnd);
  return from + (to - from) * u * u * (3 - 2 * u);
}

export type Trend = 'up' | 'down' | 'flat';

const SLOPE_DT = 0.08;
const SLOPE_MIN_ST = 0.12;

export function slopeAt(round: Round, t: number): Trend {
  const d = trackSt(round, t + SLOPE_DT) - trackSt(round, t - SLOPE_DT);
  return d > SLOPE_MIN_ST ? 'up' : d < -SLOPE_MIN_ST ? 'down' : 'flat';
}

export function trendAhead(round: Round, t: number, aheadSec = 0.8, minSt = 0.8): Trend {
  const d = trackSt(round, t + aheadSec) - trackSt(round, t);
  return d > minSt ? 'up' : d < -minSt ? 'down' : 'flat';
}

export interface Gauge {
  band: string;
  target: string;
  dotY: number;
  zeroY: number;
}

export function buildGauge(round: Round, t: number, camSt: number, gw: number, gh: number, aheadSec = 2.5, backSec = 0.5): Gauge {
  const span = round.spanSt;
  const yOf = (st: number) => gh / 2 - (Math.max(-span, Math.min(span, st)) / span) * (gh / 2 - 6);
  const xOf = (tau: number) => ((tau - t + backSec) / (aheadSec + backSec)) * gw;
  const tol = round.config.tolSt;
  const step = 0.05;
  let target = '';
  let open = false;
  for (let tau = t - backSec; tau <= t + aheadSec + 1e-9; tau += step) {
    target += `${open ? 'L' : 'M'}${fmt(xOf(tau))} ${fmt(yOf(trackSt(round, tau)))}`;
    open = true;
  }
  let band = '';
  const flush = (a: string[], b: string[]) => {
    if (a.length > 1) band += `M${a.join('L')}L${b.reverse().join('L')}Z`;
  };
  const tops: string[] = [];
  const bots: string[] = [];
  for (let tau = t - backSec; tau <= t + aheadSec + 1e-9; tau += step) {
    const st = trackSt(round, tau);
    const x = fmt(xOf(tau));
    if (isActive(round, tau)) {
      tops.push(`${x} ${fmt(yOf(st + tol))}`);
      bots.push(`${x} ${fmt(yOf(st - tol))}`);
    } else {
      flush(tops, bots);
      tops.length = 0;
      bots.length = 0;
    }
  }
  flush(tops, bots);
  return { band, target, dotY: yOf(camSt), zeroY: yOf(0) };
}

export function isActive(round: Round, t: number): boolean {
  const p = phraseAt(round, t);
  return p !== null && !p.warmup;
}

function lateral(x: number): number {
  return 0.9 * Math.sin(0.45 * x) + 0.45 * Math.sin(1.05 * x + 1.3);
}

function lateralSlope(x: number): number {
  return 0.9 * 0.45 * Math.cos(0.45 * x) + 0.45 * 1.05 * Math.cos(1.05 * x + 1.3);
}

function fmt(n: number): string {
  return (Math.round(n * 10) / 10).toString();
}

const U_TRACK = 0.7;
const U_DEV = 0.15;
const LOOK_DIST = 2.8;
const MAX_LOOK_SEC = 6;
const SPEED_K = 0.75;
const V_MIN = 0.45;
const V_MAX = 1.9;
const MAX_PITCH = 0.5;
const PITCH_FOLLOW = 0.55;
const COURSE_DT = 0.02;

interface Course { t0: number; dist: Float64Array; }
const courses = new WeakMap<Round, Course>();

function course(round: Round): Course {
  const cached = courses.get(round);
  if (cached) return cached;
  const t0 = -1;
  const n = Math.ceil((round.duration + MAX_LOOK_SEC + 2 - t0) / COURSE_DT) + 1;
  const dist = new Float64Array(n);
  for (let i = 1; i < n; i++) {
    const tau = t0 + (i - 1) * COURSE_DT;
    const climb = ((trackSt(round, tau + COURSE_DT) - trackSt(round, tau)) * U_TRACK) / COURSE_DT;
    const v = Math.min(V_MAX, Math.max(V_MIN, 1 - SPEED_K * climb));
    dist[i] = dist[i - 1] + v * COURSE_DT;
  }
  const c = { t0, dist };
  courses.set(round, c);
  return c;
}

export function distAt(round: Round, tau: number): number {
  const { t0, dist } = course(round);
  const x = (tau - t0) / COURSE_DT;
  if (x <= 0) return dist[0] + (tau - t0);
  const i = Math.floor(x);
  if (i >= dist.length - 1) return dist[dist.length - 1] + (x - (dist.length - 1)) * COURSE_DT;
  return dist[i] + (dist[i + 1] - dist[i]) * (x - i);
}

export function speedAt(round: Round, t: number): number {
  return (distAt(round, t + 0.05) - distAt(round, t - 0.05)) / 0.1;
}

export function lookAheadTau(round: Round, t: number): number {
  const goal = distAt(round, t) + LOOK_DIST;
  let tau = t;
  while (tau < t + MAX_LOOK_SEC && distAt(round, tau) < goal) tau += COURSE_DT;
  return tau;
}

export function pitchAt(round: Round, t: number): number {
  const a = t - 0.1;
  const b = t + 0.45;
  const rise = (trackSt(round, b) - trackSt(round, a)) * U_TRACK;
  const run = distAt(round, b) - distAt(round, a);
  return Math.max(-MAX_PITCH, Math.min(MAX_PITCH, PITCH_FOLLOW * Math.atan2(rise, run)));
}

function lateralCurve(x: number): number {
  return -0.9 * 0.45 * 0.45 * Math.sin(0.45 * x) - 0.45 * 1.05 * 1.05 * Math.sin(1.05 * x + 1.3);
}

export function rollAt(round: Round, t: number): number {
  return Math.max(-9, Math.min(9, lateralCurve(distAt(round, t + 0.3)) * 14));
}

export interface Projector {
  horizonY: number;
  noseY: number;
  point(tau: number, side: number, up?: number): { x: number; y: number; s: number } | null;
  ground(tau: number, side: number): { x: number; y: number; s: number } | null;
}

export function makeProjector(round: Round, t: number, camSt: number, w: number, h: number): Projector {
  const baseY = h * 0.36;
  const noseY = h * 0.82;
  const k = ((noseY - baseY) * Z0) / EYE;
  const cx = w / 2;
  const d0 = distAt(round, t);
  const x0 = lateral(d0);
  const slope = lateralSlope(d0);
  const here = trackSt(round, t);
  const eye = here * U_TRACK + (camSt - here) * U_DEV + EYE;
  const theta = pitchAt(round, t);
  const c = Math.cos(theta);
  const sn = Math.sin(theta);
  const groundW = -(round.spanSt + 3) * U_TRACK;
  const project = (tau: number, side: number, hw: number) => {
    if (tau < t) return null;
    const a = distAt(round, tau) - d0;
    const b = hw - eye + EYE;
    const z = Z0 + a * c + b * sn;
    const y = -EYE - a * sn + b * c;
    if (z < 0.08) return null;
    const sc = k / z;
    const lat = lateral(d0 + a) - x0 - slope * a + side;
    return { x: cx + lat * sc, y: baseY - y * sc, s: sc };
  };
  return {
    horizonY: baseY + k * Math.tan(theta),
    noseY,
    point(tau, side, up = 0) {
      return project(tau, side, trackSt(round, tau) * U_TRACK + up);
    },
    ground(tau, side) {
      return project(tau, side, groundW);
    },
  };
}

function quad(a: { x: number; y: number }, b: { x: number; y: number }, c: { x: number; y: number }, d: { x: number; y: number }): string {
  return `M${fmt(a.x)} ${fmt(a.y)}L${fmt(b.x)} ${fmt(b.y)}L${fmt(c.x)} ${fmt(c.y)}L${fmt(d.x)} ${fmt(d.y)}Z`;
}

export function buildScene(round: Round, t: number, camSt: number, w: number, h: number, coinsGot: readonly boolean[]): Scene {
  const pr = makeProjector(round, t, camSt, w, h);
  const end = lookAheadTau(round, t);
  const span = end - t;

  const taus: number[] = [];
  for (let i = 0; i <= SAMPLES; i++) taus.push(t + span * Math.pow(i / SAMPLES, 1.6));

  const left: string[] = [];
  const right: string[] = [];
  const bedL: { x: number; y: number }[] = [];
  const bedR: { x: number; y: number }[] = [];
  const horizonTau: number[] = [];
  const horizonY: number[] = [];
  let minY = Infinity;
  let leftOpen = false;
  let rightOpen = false;
  for (const tau of taus) {
    const c = pr.point(tau, 0);
    const l = pr.point(tau, -GAUGE);
    const r = pr.point(tau, GAUGE);
    const bl = pr.point(tau, -BED);
    const br = pr.point(tau, BED);
    if (!c || !l || !r || !bl || !br) continue;
    const hidden = c.y > minY + 1;
    minY = Math.min(minY, c.y);
    horizonTau.push(tau);
    horizonY.push(minY);
    if (hidden) {
      leftOpen = false;
      rightOpen = false;
      continue;
    }
    left.push(`${leftOpen ? 'L' : 'M'}${fmt(l.x)} ${fmt(l.y)}`);
    right.push(`${rightOpen ? 'L' : 'M'}${fmt(r.x)} ${fmt(r.y)}`);
    leftOpen = true;
    rightOpen = true;
    bedL.push(bl);
    bedR.push(br);
  }
  const occluded = (tau: number, y: number): boolean => {
    let lim = Infinity;
    for (let i = 0; i < horizonTau.length && horizonTau[i] <= tau; i++) lim = horizonY[i];
    return y > lim + 1;
  };
  let bed = '';
  if (bedL.length > 1) {
    bed = `M${fmt(bedL[0].x)} ${fmt(bedL[0].y)}`;
    for (let i = 1; i < bedL.length; i++) bed += `L${fmt(bedL[i].x)} ${fmt(bedL[i].y)}`;
    for (let i = bedR.length - 1; i >= 0; i--) bed += `L${fmt(bedR[i].x)} ${fmt(bedR[i].y)}`;
    bed += 'Z';
  }

  let ties = '';
  let tiesActive = '';
  let tiesUp = '';
  let tiesDown = '';
  const firstTie = Math.ceil(t / TIE_STEP_SEC) * TIE_STEP_SEC;
  for (let tau = end - (end % TIE_STEP_SEC); tau >= firstTie - 1e-9; tau -= TIE_STEP_SEC) {
    const a = pr.point(tau, -BED * 0.9);
    const b = pr.point(tau, BED * 0.9);
    const c = pr.point(tau + TIE_DEPTH_SEC, BED * 0.9);
    const d = pr.point(tau + TIE_DEPTH_SEC, -BED * 0.9);
    const mid = pr.point(tau, 0);
    if (!a || !b || !c || !d || !mid || occluded(tau, mid.y)) continue;
    const q = quad(a, b, c, d);
    if (!isActive(round, tau)) ties += q;
    else {
      const trend = slopeAt(round, tau);
      if (trend === 'up') tiesUp += q;
      else if (trend === 'down') tiesDown += q;
      else tiesActive += q;
    }
  }

  let pillars = '';
  const firstPillar = Math.ceil(t / PILLAR_STEP_SEC) * PILLAR_STEP_SEC;
  for (let tau = firstPillar; tau <= end; tau += PILLAR_STEP_SEC) {
    const top = pr.point(tau, 0);
    const foot = pr.ground(tau, 0);
    if (!top || !foot || occluded(tau, top.y)) continue;
    const half = PILLAR_W * top.s;
    const halfFoot = PILLAR_W * foot.s;
    pillars += `M${fmt(top.x - half)} ${fmt(top.y)}L${fmt(top.x + half)} ${fmt(top.y)}L${fmt(foot.x + halfFoot)} ${fmt(foot.y)}L${fmt(foot.x - halfFoot)} ${fmt(foot.y)}Z`;
  }

  const coins: CoinSprite[] = [];
  for (let i = round.stars.length - 1; i >= 0; i--) {
    const tau = round.stars[i];
    if (coinsGot[i] || tau < t || tau > end) continue;
    const p = pr.point(tau, 0, COIN_UP);
    const base = pr.point(tau, 0);
    if (!p || !base || occluded(tau, base.y)) continue;
    coins.push({ i, x: p.x, y: p.y, r: COIN_R * p.s });
  }

  const near = pr.point(t, 0) as { y: number };
  return {
    horizonY: pr.horizonY,
    roll: rollAt(round, t),
    speed: speedAt(round, t),
    noseY: pr.noseY,
    railNearY: near.y,
    bed,
    ties,
    tiesActive,
    tiesUp,
    tiesDown,
    rails: left.join('') + right.join(''),
    pillars,
    coins,
  };
}
