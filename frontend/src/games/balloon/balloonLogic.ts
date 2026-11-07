// 게임 판정: 기준선 대비 dB 차이로 풍선 크기 증감, 유지시간 누적, 통과 여부 (순수 함수)

/** 목표 음량 = 기준 음량 + TARGET_DELTA_DB */
export const TARGET_DELTA_DB = 6;
/** 목표 음량 이상을 이만큼(초) 연속 유지하면 통과 */
export const TARGET_HOLD_SECONDS = 3;
/** 풍선 크기 변화 속도 (크기 1.0 = 최대, 초당) */
export const GROW_PER_SEC = 0.4;
export const SHRINK_PER_SEC = 0.6;

export type BalloonState = {
  /** 풍선 크기 0 ~ 1 */
  size: number;
  /** 목표 음량 이상을 연속 유지한 시간(초) */
  holdTime: number;
  passed: boolean;
};

export const initialBalloonState: BalloonState = {
  size: 0,
  holdTime: 0,
  passed: false,
};

/**
 * 프레임 하나를 처리해서 다음 상태를 돌려준다.
 * @param dbDiff 현재 음량 - 기준 음량 (dB)
 * @param dt 프레임 길이(초)
 */
export function stepBalloon(
  state: BalloonState,
  dbDiff: number,
  dt: number,
  targetDeltaDb: number = TARGET_DELTA_DB,
  targetHoldSeconds: number = TARGET_HOLD_SECONDS,
): BalloonState {
  if (state.passed) return state;

  const above = dbDiff >= targetDeltaDb;
  const size = clamp01(
    state.size + (above ? GROW_PER_SEC : -SHRINK_PER_SEC) * dt,
  );
  // 목표 미만으로 떨어지면 유지시간은 처음부터 다시
  const holdTime = above ? state.holdTime + dt : 0;

  return { size, holdTime, passed: holdTime >= targetHoldSeconds };
}

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}
