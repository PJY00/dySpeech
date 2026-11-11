// [임시] 잡음 바닥(조용할 때의 음량)과 "소리가 났다"는 판단 기준 (순수 함수). 앱 쪽 측정 블록이 잡음 바닥을 주므로 합칠 때 지운다.

export const VOICING_MARGIN_DB = 10;
/** 조용한 구간 프레임들의 하위 백분위를 잡음 바닥으로 본다 */
export const NOISE_PERCENTILE = 20;

/** 선형 보간 백분위 (p: 0~100) */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const pos = (Math.min(100, Math.max(0, p)) / 100) * (sorted.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** 조용한 구간의 프레임별 dBFS들 → 잡음 바닥 dBFS */
export function estimateNoiseFloor(quietLevelsDb: number[]): number {
  return percentile(quietLevelsDb, NOISE_PERCENTILE);
}

/** 이 값 이상이면 소리가 난 프레임 (dBFS) */
export function voicingThresholdDb(noiseFloorDb: number): number {
  return noiseFloorDb + VOICING_MARGIN_DB;
}
