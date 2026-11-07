// 기본주파수 추적(pitchy, 맥러드 방식)과 보정. docs/metrics-spec.md 5장(기기 권장)과 4.6(서버 보정 순서)을 따른다.

import { PitchDetector } from 'pitchy';

import type { HzRange } from '../session/types';

/** 분석 창 길이: 48 kHz에서 2048표본(약 43 ms). 다른 샘플레이트는 같은 시간 길이로 맞춘다 */
export const WINDOW_AT_48K = 2048;
/** 명료도가 이보다 낮은 값은 버린다 */
export const CLARITY_MIN = 0.85;
export const DEFAULT_RANGE_HZ: HzRange = { min: 75, max: 500 };
const CALIBRATION_WIDEN = { low: 0.8, high: 1.2 };
const LIMIT_HZ: HzRange = { min: 50, max: 800 };
/** 유성 구간 앞뒤에서 버리는 프레임(20 ms) */
const EDGE_FRAMES = 2;
/** 중앙값에서 이만큼(반음) 벗어나면 옥타브 오류로 본다 */
const OCTAVE_ERROR_ST = 10;
const MEDIAN_FRAMES = 5;

/** 백분위(선형 보간, numpy 기본과 같음) */
export function percentile(values: ArrayLike<number>, p: number): number {
  if (!values.length) return NaN;
  const a = Array.from(values).sort((x, y) => x - y);
  const idx = (Math.min(100, Math.max(0, p)) / 100) * (a.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return a[lo] + (a[hi] - a[lo]) * (idx - lo);
}

/** st(반음 값) = 12 × log₂(F(기본주파수, Hz) ÷ F_ref(기준 기본주파수, Hz)) */
export function semitones(hz: number, refHz: number): number {
  return 12 * Math.log2(hz / refHz);
}

/**
 * 기본주파수 탐색 범위(서버 규칙, metrics-spec.md 4.6)
 * - 아래쪽: 캘리브레이션 최저 × 0.8과 75 Hz 중 낮은 쪽(50 Hz 이상)
 * - 위쪽: 캘리브레이션 최고 × 1.2(800 Hz 이하)
 * - 캘리브레이션이 없거나 위쪽이 아래쪽의 2배 이하이면 75~500 Hz
 */
export function searchRange(calibration: HzRange | null): HzRange {
  if (!calibration) return { ...DEFAULT_RANGE_HZ };
  const min = Math.max(Math.min(calibration.min * CALIBRATION_WIDEN.low, DEFAULT_RANGE_HZ.min), LIMIT_HZ.min);
  const max = Math.min(calibration.max * CALIBRATION_WIDEN.high, LIMIT_HZ.max);
  return max > 2 * min ? { min, max } : { ...DEFAULT_RANGE_HZ };
}

/**
 * 프레임마다 기본주파수(Hz)를 구한다. 프레임 i의 중심은 analysis.frameLevels와 같다(i × 10 ms + 15 ms)
 * 명료도 0.85 미만, 범위 밖, voiced가 거짓인 프레임은 null
 */
export function pitchTrack(
  samples: Float32Array,
  sampleRate: number,
  frameCount: number,
  range: HzRange,
  voiced?: ArrayLike<boolean>,
): (number | null)[] {
  const window = Math.round((WINDOW_AT_48K * sampleRate) / 48000);
  const hop = Math.round(0.01 * sampleRate);
  const halfFrame = Math.round(0.03 * sampleRate) / 2;
  const detector = PitchDetector.forFloat32Array(window);
  const out: (number | null)[] = new Array(frameCount).fill(null);
  for (let i = 0; i < frameCount; i++) {
    if (voiced && !voiced[i]) continue;
    const start = Math.round(i * hop + halfFrame - window / 2);
    if (start < 0 || start + window > samples.length) continue;
    const [hz, clarity] = detector.findPitch(samples.subarray(start, start + window), sampleRate);
    if (clarity >= CLARITY_MIN && hz >= range.min && hz <= range.max) out[i] = hz;
  }
  return out;
}

function voicedRuns(f0: (number | null)[]): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  for (let i = 0; i <= f0.length; i++) {
    const v = i < f0.length && f0[i] !== null;
    if (v && start < 0) start = i;
    if (!v && start >= 0) {
      out.push([start, i]);
      start = -1;
    }
  }
  return out;
}

/**
 * 서버와 같은 순서로 보정한다
 * 1. 유성 구간 앞뒤 20 ms(2프레임)를 버림
 * 2. 중앙값에서 10반음 이상 벗어난 값은 2배나 1/2로 접고, 접어도 10반음 이상이면 버림
 * 3. 유성 구간 안에서 5프레임 중앙값 필터(끝은 가장자리 값을 늘려 씀)
 */
export function cleanF0(raw: (number | null)[]): (number | null)[] {
  const f0 = raw.slice();
  for (const [s, e] of voicedRuns(f0)) {
    for (let i = s; i < Math.min(s + EDGE_FRAMES, e); i++) f0[i] = null;
    for (let i = Math.max(e - EDGE_FRAMES, s); i < e; i++) f0[i] = null;
  }
  const voiced = f0.filter((v): v is number => v !== null);
  if (!voiced.length) return f0;
  const med = percentile(voiced, 50);
  for (let i = 0; i < f0.length; i++) {
    const v = f0[i];
    if (v === null || Math.abs(semitones(v, med)) < OCTAVE_ERROR_ST) continue;
    const folded = Math.abs(semitones(v * 2, med)) < Math.abs(semitones(v / 2, med)) ? v * 2 : v / 2;
    f0[i] = Math.abs(semitones(folded, med)) < OCTAVE_ERROR_ST ? folded : null;
  }
  const out = f0.slice();
  const half = Math.floor(MEDIAN_FRAMES / 2);
  for (const [s, e] of voicedRuns(f0)) {
    for (let i = s; i < e; i++) {
      const win: number[] = [];
      for (let k = i - half; k <= i + half; k++) win.push(f0[Math.min(e - 1, Math.max(s, k))] as number);
      out[i] = percentile(win, 50);
    }
  }
  return out;
}
