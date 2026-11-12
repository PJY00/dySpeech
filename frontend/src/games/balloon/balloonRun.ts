import { Platform } from 'react-native';

import type { GameRun } from '../../session/types';
import { uuidv4 } from '../../session/uuid';
import { BalloonState, BalloonTarget, diffStats, ROUND_SECONDS, summarizeRound } from './balloonLogic';

export type BalloonGameName = 'balloon' | 'candle';

export interface BalloonParams {
  target_delta_db: number;
  target_hold_s: number;
  round_limit_s: number;
  baseline_db: number;
  noise_floor_dbfs: number;
  voiced_margin_db: number;
  platform: string;
}

export interface BalloonResult {
  passed: boolean;
  elapsed_s: number;
  above_target_s: number;
  best_hold_s: number;
  voiced_coverage_pct: number;
  /** 목표 음량 이상이었던 시간 ÷ 전체 시간(%). success_rate_pct는 목표 유지 달성도 */
  above_target_pct: number;
  /** 소리 낸 프레임의 기준 대비 dB 차이 평균·중앙값. 소리를 내지 않았으면 null */
  mean_diff_db: number | null;
  median_diff_db: number | null;
}

export type BalloonGameRun = GameRun & { params: BalloonParams; result: BalloonResult };

const round1 = (x: number) => Math.round(x * 10) / 10;

export function buildBalloonRun(args: {
  game: BalloonGameName;
  state: BalloonState;
  diffsDb: number[];
  level: number;
  roundIndex: number;
  startedAt: string;
  target: BalloonTarget;
  baselineDb: number;
  noiseFloorDbfs: number;
  voicedMarginDb: number;
}): BalloonGameRun {
  const { state, level, target } = args;
  const summary = summarizeRound(state, level, target);
  const stats = diffStats(args.diffsDb);

  return {
    game_run_id: uuidv4(),
    game: args.game,
    level,
    round_index: args.roundIndex,
    started_at: args.startedAt,
    params: {
      target_delta_db: target.deltaDb,
      target_hold_s: target.holdSeconds,
      round_limit_s: ROUND_SECONDS,
      baseline_db: round1(args.baselineDb),
      noise_floor_dbfs: round1(args.noiseFloorDbfs),
      voiced_margin_db: args.voicedMarginDb,
      platform: Platform.OS,
    },
    success_rate_pct: summary.successRatePct,
    decision: summary.decision,
    decision_reason: summary.reason,
    result: {
      passed: state.passed,
      elapsed_s: round1(state.elapsed),
      above_target_s: round1(state.aboveTime),
      best_hold_s: round1(state.bestHold),
      voiced_coverage_pct: summary.voicedCoveragePct,
      above_target_pct: summary.aboveTargetPct,
      mean_diff_db: stats ? round1(stats.mean) : null,
      median_diff_db: stats ? round1(stats.median) : null,
    },
  };
}
