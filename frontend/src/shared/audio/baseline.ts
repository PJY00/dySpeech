// [임시] 사용자의 기준선(TempCalibration이 만든다). 앱 쪽 Baselines(측정 블록 결과)로 대체되므로 합칠 때 지운다.
export type Baseline = {
  /** 기준 음량 (dBFS, 소리 낸 구간의 중앙값) */
  baselineDb: number;
  /** 잡음 바닥 (dBFS, 조용할 때의 음량) */
  noiseFloorDb: number;
};
