// 호흡 훈련(풍선, 촛불) 판정: 기준 음량 대비 dB 차이로 크기 증감, 유지시간 누적, 통과 여부,
// 라운드 결과(성공률, 다음 단계 판단)
//
// 아래 단계표와 판단 기준의 숫자는 모두 임시 값이다. 임상 근거가 아직 없다.
// 다음 단계를 저장하는 일은 앱이 한다. 게임은 decision(올림/내림/유지/보류)만 정한다.
import { median } from '../../shared/audio/audio'; // 앱 구조가 합쳐지면 '../../measurement/analysis'
import type { Decision } from '../../shared/api/gameRun'; // 앱 구조가 합쳐지면 '../../session/types'

/** 목표치: 기준 음량 + deltaDb 이상을 holdSeconds 동안 연속 유지 */
export type BalloonTarget = { deltaDb: number; holdSeconds: number };
export type BalloonLevel = BalloonTarget & { level: number };

// 목표 차이는 1dB씩만 오르고, 유지 시간은 사용자의 최대발성지속시간(MPT)에 비례한다.
const LEVEL_SPECS = [
  { level: 1, deltaDb: 6, holdRatio: 0.15 },
  { level: 2, deltaDb: 7, holdRatio: 0.2 },
  { level: 3, deltaDb: 8, holdRatio: 0.25 },
  { level: 4, deltaDb: 9, holdRatio: 0.3 },
  { level: 5, deltaDb: 10, holdRatio: 0.35 },
] as const;
export const MAX_LEVEL = LEVEL_SPECS.length;

/** 최대발성지속시간을 모를 때(측정 블록이 값을 못 구했을 때) 쓰는 값(초) */
export const DEFAULT_MPT_S = 10;
/** 유지 시간의 하한·상한(초) */
export const MIN_HOLD_S = 1.5;
export const MAX_HOLD_S = 5;

/** 단계의 목표치. 유지 시간 = MPT × 단계별 비율(하한·상한 안으로, 0.5초 단위) */
export function getLevel(level: number, maxPhonationTimeS?: number | null): BalloonLevel {
  const spec = LEVEL_SPECS[Math.min(MAX_LEVEL, Math.max(1, Math.round(level))) - 1];
  const mpt = maxPhonationTimeS != null && maxPhonationTimeS > 0 ? maxPhonationTimeS : DEFAULT_MPT_S;
  const hold = Math.round(mpt * spec.holdRatio * 2) / 2;
  return {
    level: spec.level,
    deltaDb: spec.deltaDb,
    holdSeconds: Math.min(MAX_HOLD_S, Math.max(MIN_HOLD_S, hold)),
  };
}

export const ROUND_SECONDS = 15;

export const GRACE_S = 0.3;

export const GROW_PER_SEC = 0.4;
export const SHRINK_PER_SEC = 0.6;

export type BalloonState = {
  /** 풍선 크기 0 ~ 1 */
  size: number;
  /** 목표 음량 이상을 이어서 유지한 시간(초) */
  holdTime: number;
  /** 지금 목표 아래로 이어서 떨어져 있는 시간(초) */
  belowTime: number;
  /** 이번 라운드에서 가장 길게 유지한 시간(초) */
  bestHold: number;
  passed: boolean;
  /** 라운드 시작 후 지난 시간(초) */
  elapsed: number;
  /** 목표 음량 이상이었던 시간 합(초) */
  aboveTime: number;
  /** 소리가 났던(잡음 바닥 + 10dB 이상) 시간 합(초) */
  voicedTime: number;
};

export const initialBalloonState: BalloonState = {
  size: 0,
  holdTime: 0,
  belowTime: 0,
  bestHold: 0,
  passed: false,
  elapsed: 0,
  aboveTime: 0,
  voicedTime: 0,
};

export function isRoundOver(state: BalloonState): boolean {
  return state.passed || state.elapsed >= ROUND_SECONDS;
}

/**
 * 프레임 하나를 처리해서 다음 상태를 돌려준다.
 * @param dbDiff 현재 음량 - 기준 음량 (dB)
 * @param dt 프레임 길이(초)
 * @param voiced 소리가 난 프레임인지 (성공률 계산과 보류 판단에만 쓴다)
 */
export function stepBalloon(
  state: BalloonState,
  dbDiff: number,
  dt: number,
  target: BalloonTarget,
  voiced: boolean = true,
): BalloonState {
  if (isRoundOver(state)) return state;

  const above = dbDiff >= target.deltaDb;
  const belowTime = above ? 0 : state.belowTime + dt;
  // 짧은 끊김(GRACE_S 이하)은 봐준다: 그동안 유지 시간과 크기는 그대로 두고, 더 길어지면 처음부터 다시
  const inGrace = !above && belowTime <= GRACE_S;
  const size = inGrace
    ? state.size
    : clamp01(state.size + (above ? GROW_PER_SEC : -SHRINK_PER_SEC) * dt);
  const holdTime = above ? state.holdTime + dt : inGrace ? state.holdTime : 0;

  return {
    size,
    holdTime,
    belowTime,
    bestHold: Math.max(state.bestHold, holdTime),
    passed: holdTime >= target.holdSeconds,
    elapsed: state.elapsed + dt,
    aboveTime: state.aboveTime + (above ? dt : 0),
    voicedTime: state.voicedTime + (voiced ? dt : 0),
  };
}


/** 소리 낸 시간 비율이 이보다 낮으면 반복으로 치지 않는다 (%) */
export const MIN_VOICED_PCT = 30;

export type RoundSummary = {
  /** 서버로 보내는 성공률(%, 0~100) = 목표 유지 시간 대비 가장 오래 유지한 시간(100 상한) */
  successRatePct: number;
  aboveTargetPct: number;
  voicedCoveragePct: number;
  decision: Decision;
  reason: string;
};

const round1 = (x: number) => Math.round(x * 10) / 10;

/**
 * - 목표를 채웠으면 다음 단계(up), 최고 단계면 그대로(hold)
 * - 못 채웠으면 그 단계 그대로(hold). 단계를 내리지 않는다
 * - 소리가 거의 안 난 라운드는 세지 않는다(deferred)
 */
export function summarizeRound(state: BalloonState, level: number, target: BalloonTarget): RoundSummary {
  const total = state.elapsed;
  const successRatePct =
    target.holdSeconds > 0 ? round1(Math.min(100, (state.bestHold / target.holdSeconds) * 100)) : 0;
  const aboveTargetPct = total > 0 ? round1((state.aboveTime / total) * 100) : 0;
  const voicedCoveragePct = total > 0 ? round1((state.voicedTime / total) * 100) : 0;
  const base = { successRatePct, aboveTargetPct, voicedCoveragePct };

  if (voicedCoveragePct < MIN_VOICED_PCT) {
    return {
      ...base,
      decision: 'deferred',
      reason: '소리가 잘 안 들려서 이번에는 세지 않았어요',
    };
  }
  if (!state.passed) {
    return { ...base, decision: 'hold', reason: '이번 단계를 한 번 더 해 봐요' };
  }
  return level < MAX_LEVEL
    ? { ...base, decision: 'up', reason: '목표를 채워서 다음 단계로 가요' }
    : { ...base, decision: 'hold', reason: '목표를 채웠어요. 가장 높은 단계예요' };
}

/** 소리 낸 프레임들의 기준 대비 dB 차이 → 평균과 중앙값. 값이 없으면 null */
export function diffStats(diffsDb: number[]): { mean: number; median: number } | null {
  if (diffsDb.length === 0) return null;
  const mean = diffsDb.reduce((a, b) => a + b, 0) / diffsDb.length;
  return { mean, median: median(diffsDb) };
}

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}
