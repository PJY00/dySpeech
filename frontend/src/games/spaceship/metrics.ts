import { mean, std } from './audio/pitchMath';
import type { SpaceshipBaseline } from './baseline';
import { deviationAt, MAX_LEVEL, type Round } from './contours';
import type { GameState } from './engine';
import { uuidv4 } from './uuid';

export interface SpaceshipGameRun {
  game_run_id: string;
  game: 'spaceship';
  level: number;
  round_index: number;
  started_at: string;
  params: SpaceshipParams;
  success_rate_pct: number;
  decision: 'up' | 'down' | 'hold' | 'deferred';
  decision_reason: string;
  result: SpaceshipResultDetail;
}

export interface SpaceshipParams {
  seed: number;
  phrase_count: number;
  patterns: string[];
  amplitude_semitones: number;
  lane_half_width_semitones: number;
  phrase_s: number;
  rest_s: number;
  reaction_allowance_s: number;
  f0_median_hz: number;
  noise_floor_dbfs: number;
  f0_range_hz: [number, number];
  sample_rate: number | null;
  window_s: number;
  hop_s: number;
  clarity_threshold: number;
  platform: string;
}

export interface SpaceshipResultDetail {
  passed: boolean;
  curve_rmse_semitones: number | null;
  pass_rmse_semitones: number;
  f0_sd_semitones: number | null;
  target_sd_semitones: number;
  mean_bias_semitones: number | null;
  estimation_failure_pct: number | null;
  voiced_coverage_pct: number;
  stars_collected: number;
  stars_total: number;
  hits: number;
  best_combo: number;
  live_noise_floor_dbfs: number;
}

const MIN_COVERAGE = 0.5;
const PASS_RMSE_RATIO = 0.7;
const PASS_RMSE_FLOOR_ST = 0.5;
const UP_LANE_RATIO = 0.7;
const DOWN_LANE_RATIO = 0.35;

const round1 = (x: number) => Math.round(x * 10) / 10;
const round3 = (x: number) => Math.round(x * 1000) / 1000;
const opt = (x: number | null, f: (v: number) => number) => (x === null || !Number.isFinite(x) ? null : f(x));

export interface RunContext {
  roundIndex: number;
  startedAt: string;
  sampleRate: number | null;
  windowSec: number;
  hopSec: number;
  clarityThreshold: number;
  platform: string;
  f0RangeHz: [number, number];
  reactionAllowanceSec: number;
}

export function computeGameRun(
  s: GameState,
  round: Round,
  baseline: SpaceshipBaseline,
  ctx: RunContext,
): SpaceshipGameRun {
  const tol = round.config.tolSt;
  const phrase = s.frames.filter((f) => f.inPhrase && f.targetSt !== null);
  const voiced = phrase.filter((f) => f.voiced);
  const valid = phrase.filter((f) => f.valid && f.st !== null);

  const devs = valid
    .map((f) => deviationAt(round, f.t, f.st as number))
    .filter((d): d is number => d !== null);
  const curveRmse = devs.length ? Math.sqrt(mean(devs.map((d) => d * d))) : null;
  const f0Sd = valid.length >= 2 ? std(valid.map((f) => f.st as number)) : null;
  const targetSdRaw = std(phrase.map((f) => f.targetSt as number));
  const targetSd = Number.isFinite(targetSdRaw) ? targetSdRaw : 0;
  const failure = voiced.length ? 1 - valid.length / voiced.length : null;
  const coverage = phrase.length ? valid.length / phrase.length : 0;
  const onLane = phrase.length ? devs.filter((d) => Math.abs(d) <= tol).length / phrase.length : 0;

  const passRmse = Math.max(PASS_RMSE_FLOOR_ST, PASS_RMSE_RATIO * targetSd);
  const passed = coverage >= MIN_COVERAGE && curveRmse !== null && curveRmse <= passRmse;

  let decision: SpaceshipGameRun['decision'];
  let reason: string;
  const level = round.config.level;
  if (coverage < MIN_COVERAGE) {
    decision = 'deferred';
    reason = `소리 낸 구간 ${Math.round(coverage * 100)}%로 50% 미만이라 판정 보류`;
  } else if (passed && onLane >= UP_LANE_RATIO && level < MAX_LEVEL) {
    decision = 'up';
    reason = `통과, 길 안 ${Math.round(onLane * 100)}%로 70% 이상`;
  } else if (!passed && onLane < DOWN_LANE_RATIO && level > 1) {
    decision = 'down';
    reason = `미통과, 길 안 ${Math.round(onLane * 100)}%로 35% 미만`;
  } else {
    decision = 'hold';
    reason = passed
      ? level >= MAX_LEVEL ? '통과, 최고 단계' : `통과, 길 안 ${Math.round(onLane * 100)}%로 70% 미만`
      : level <= 1 ? '미통과, 최저 단계' : `미통과, 길 안 ${Math.round(onLane * 100)}%`;
  }

  return {
    game_run_id: uuidv4(),
    game: 'spaceship',
    level,
    round_index: ctx.roundIndex,
    started_at: ctx.startedAt,
    params: {
      seed: round.seed,
      phrase_count: round.phrases.length,
      patterns: round.phrases.map((p) => p.pattern),
      amplitude_semitones: round.config.ampSt,
      lane_half_width_semitones: tol,
      phrase_s: round.config.phraseSec,
      rest_s: round.config.restSec,
      reaction_allowance_s: ctx.reactionAllowanceSec,
      f0_median_hz: round1(baseline.f0MedianHz),
      noise_floor_dbfs: round1(baseline.noiseFloorDbfs),
      f0_range_hz: ctx.f0RangeHz,
      sample_rate: ctx.sampleRate,
      window_s: round3(ctx.windowSec),
      hop_s: ctx.hopSec,
      clarity_threshold: ctx.clarityThreshold,
      platform: ctx.platform,
    },
    success_rate_pct: round1(onLane * 100),
    decision,
    decision_reason: reason,
    result: {
      passed,
      curve_rmse_semitones: opt(curveRmse, round3),
      pass_rmse_semitones: round3(passRmse),
      f0_sd_semitones: opt(f0Sd, round3),
      target_sd_semitones: round3(targetSd),
      mean_bias_semitones: opt(devs.length ? mean(devs) : null, round3),
      estimation_failure_pct: opt(failure, (x) => round1(x * 100)),
      voiced_coverage_pct: round1(coverage * 100),
      stars_collected: s.starGot.filter(Boolean).length,
      stars_total: round.stars.length,
      hits: s.hits,
      best_combo: s.bestCombo,
      live_noise_floor_dbfs: round1(s.noise.dbfs),
    },
  };
}

export function nextLevelOf(run: SpaceshipGameRun): number {
  if (run.decision === 'up') return Math.min(MAX_LEVEL, run.level + 1);
  if (run.decision === 'down') return Math.max(1, run.level - 1);
  return run.level;
}
