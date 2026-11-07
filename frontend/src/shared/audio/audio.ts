// 음향 처리: 프레임 → RMS → dB, 중앙값 (순수 함수)

/** 무음일 때 -Infinity 대신 쓰는 하한값 */
export const MIN_DB = -100;

/** 프레임의 RMS (제곱평균제곱근). 입력은 -1.0 ~ 1.0 범위의 PCM */
export function rms(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    sum += samples[i] * samples[i];
  }
  return Math.sqrt(sum / samples.length);
}

/** RMS → dBFS (0 dB = 최대 진폭) */
export function toDb(rmsValue: number): number {
  if (rmsValue <= 0) return MIN_DB;
  return Math.max(MIN_DB, 20 * Math.log10(rmsValue));
}

/** 프레임 → dB */
export function frameToDb(samples: ArrayLike<number>): number {
  return toDb(rms(samples));
}

/** 중앙값 (기준 음량은 일시적인 큰 소리에 덜 흔들리도록 중앙값 사용) */
export function median(values: number[]): number {
  if (values.length === 0) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}
