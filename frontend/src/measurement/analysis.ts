// 측정 블록 녹음에서 기기 지표를 계산한다. 정의는 docs/metrics-spec.md 3·4장, 서버(backend/app/metrics)와 같은 규칙.
// 기기 값은 게임과 난이도에 쓰고, 서버는 저장만 한다(공식 값은 서버 값).

import type { Baselines, DeviceMeasurement, HzRange } from '../session/types';
import { cleanF0, percentile, searchRange } from './pitch';

export const FRAME_S = 0.03;
export const HOP_S = 0.01;
export const EPS = 1e-10;
/** 잡음 바닥보다 이만큼(dB) 크면 발성·발화로 본다 */
export const VOICE_MARGIN_DB = 10;
/** 이 길이(초) 이하로 끊긴 곳은 잇는다 */
export const BRIDGE_S = 0.2;
/** 이보다 짧은 조각(초)은 버린다 */
export const MIN_PIECE_S = 0.1;
/** 유성 프레임 비율이 이보다 낮으면 기본주파수 값은 신뢰 낮음 */
export const MIN_VOICED_RATIO = 0.5;
/** 캘리브레이션에 필요한 최소 유성 프레임 수(0.5초) */
export const MIN_CALIBRATION_FRAMES = 50;
/** noise_floor 녹음이 없을 때 잡음 바닥으로 쓰는 녹음 프레임 음량 백분위 */
const FALLBACK_FLOOR_PERCENTILE = 10;

export function frames(seconds: number): number {
  return Math.round(seconds / HOP_S);
}

export function median(values: ArrayLike<number>): number {
  return percentile(values, 50);
}

/**
 * 프레임 음량(dBFS). 프레임 길이 30 ms, 간격 10 ms
 * L(프레임 음량, dBFS) = 20 × log₁₀(RMS(제곱평균제곱근 진폭) + ε(10⁻¹⁰))
 */
export function frameLevels(samples: ArrayLike<number>, sampleRate: number): Float64Array {
  const hop = Math.round(HOP_S * sampleRate);
  const frame = Math.round(FRAME_S * sampleRate);
  if (samples.length < frame) return new Float64Array(0);
  const count = Math.floor((samples.length - frame) / hop) + 1;
  const prefix = new Float64Array(samples.length + 1);
  for (let i = 0; i < samples.length; i++) prefix[i + 1] = prefix[i] + samples[i] * samples[i];
  const out = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    const a = i * hop;
    const energy = prefix[a + frame] - prefix[a];
    out[i] = 20 * Math.log10(Math.sqrt(Math.max(0, energy) / frame) + EPS);
  }
  return out;
}

/** 참인 프레임이 이어진 구간 [시작, 끝) 목록 */
export function runs(mask: ArrayLike<boolean>): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] && start < 0) start = i;
    if (!mask[i] && start >= 0) {
      out.push([start, i]);
      start = -1;
    }
  }
  if (start >= 0) out.push([start, mask.length]);
  return out;
}

/** 공백이 maxGap 프레임 이하인 이웃 구간을 잇는다 */
export function bridge(segments: [number, number][], maxGap: number): [number, number][] {
  const merged: [number, number][] = [];
  for (const [s, e] of segments) {
    const last = merged[merged.length - 1];
    if (last && s - last[1] <= maxGap) last[1] = e;
    else merged.push([s, e]);
  }
  return merged;
}

export interface Phonation {
  start: number;
  end: number;
  durationS: number;
  /** "아" 음량: 구간 안 실제 발성 프레임 음량의 중앙값(dBFS) */
  medianLevel: number;
}

/** 발성(음량 > 잡음 바닥 + 10 dB) 구간 중 가장 긴 것. 기본주파수로 판정하지 않는다(쉰 목소리도 발성) */
export function longestPhonation(levels: ArrayLike<number>, noiseFloor: number): Phonation | null {
  const above = Array.from(levels, (l) => l > noiseFloor + VOICE_MARGIN_DB);
  const segments = bridge(runs(above), frames(BRIDGE_S)).filter(([s, e]) => e - s >= frames(MIN_PIECE_S));
  if (!segments.length) return null;
  let best = segments[0];
  for (const seg of segments) if (seg[1] - seg[0] > best[1] - best[0]) best = seg;
  const voiced: number[] = [];
  for (let i = best[0]; i < best[1]; i++) if (above[i]) voiced.push(levels[i]);
  return { start: best[0], end: best[1], durationS: (best[1] - best[0]) * HOP_S, medianLevel: median(voiced) };
}

/** 발화 프레임(음량 > 잡음 바닥 + 10 dB, 0.25초보다 짧은 공백은 이음). 기기는 발화를 음량으로만 나눈다 */
export function speechMask(levels: ArrayLike<number>, noiseFloor: number): boolean[] {
  const above = Array.from(levels, (l) => l > noiseFloor + VOICE_MARGIN_DB);
  const mask = new Array<boolean>(levels.length).fill(false);
  for (const [s, e] of bridge(runs(above), frames(0.25) - 1)) for (let i = s; i < e; i++) mask[i] = true;
  return mask;
}

export interface SentencePitch {
  f0MedianHz: number;
  voicedRatio: number;
  lowReliability: boolean;
}

/** 평가 문장: 발화 프레임 안 보정한 유성 기본주파수의 중앙값 */
export function sentencePitch(levels: ArrayLike<number>, rawF0: (number | null)[], noiseFloor: number): SentencePitch | null {
  const mask = speechMask(levels, noiseFloor);
  const f0 = cleanF0(rawF0);
  const values: number[] = [];
  let speech = 0;
  for (let i = 0; i < mask.length; i++) {
    if (!mask[i]) continue;
    speech += 1;
    const v = f0[i];
    if (v !== null && v !== undefined) values.push(v);
  }
  if (!values.length || !speech) return null;
  const voicedRatio = values.length / speech;
  return { f0MedianHz: median(values), voicedRatio, lowReliability: voicedRatio < MIN_VOICED_RATIO };
}

/** 캘리브레이션 글라이드: 보정한 유성 기본주파수의 5·95백분위. 유성 프레임이 0.5초보다 적으면 null */
export function calibrationRange(rawF0: (number | null)[]): HzRange | null {
  const values = cleanF0(rawF0).filter((v): v is number => v !== null);
  if (values.length < MIN_CALIBRATION_FRAMES) return null;
  return { min: percentile(values, 5), max: percentile(values, 95) };
}

export interface BlockInput {
  /** noise_floor 녹음의 프레임 음량 */
  noise: Float64Array | null;
  /** mpt_trial 녹음들의 프레임 음량 */
  trials: Float64Array[];
  /** eval_sentence 녹음의 프레임 음량과 기본주파수(보정 전, searchRange 범위로 구함) */
  sentence: { levels: Float64Array; f0: (number | null)[] } | null;
  /** 이번 회차 캘리브레이션 결과(첫 회차). 없으면 null */
  calibration: HzRange | null;
  /** 저장된 캘리브레이션(이전 회차). 없으면 null */
  storedCalibration: HzRange | null;
}

export interface BlockResult {
  measurements: DeviceMeasurement[];
  baselines: Omit<Baselines, 'sessionId' | 'measuredAt'>;
  notes: string[];
}

const round = (x: number, digits: number) => Math.round(x * 10 ** digits) / 10 ** digits;

/** 측정 블록 전체에서 기기 지표 4개(noise_floor, max_phonation_time, sustained_level, f0_median)와 기준값을 구한다 */
export function computeBlock(input: BlockInput): BlockResult {
  const notes: string[] = [];
  const measurements: DeviceMeasurement[] = [];
  const noNoise = input.noise === null || input.noise.length === 0;
  const floorFor = (levels: Float64Array) =>
    noNoise ? percentile(levels, FALLBACK_FLOOR_PERCENTILE) : median(input.noise as Float64Array);
  if (noNoise) notes.push('잡음 바닥 녹음이 없어, 녹음마다 프레임 음량 하위 10백분위를 잡음 바닥으로 썼다');

  let noiseFloorDbfs: number | null = null;
  if (!noNoise) {
    noiseFloorDbfs = median(input.noise as Float64Array);
    measurements.push({ metric: 'noise_floor', value: round(noiseFloorDbfs, 2), unit: 'dBFS', low_reliability: false });
  }

  let best: Phonation | null = null;
  for (const levels of input.trials) {
    const p = longestPhonation(levels, floorFor(levels));
    if (p && (!best || p.durationS > best.durationS)) best = p;
  }
  if (best) {
    measurements.push({ metric: 'max_phonation_time', value: round(best.durationS, 2), unit: 's', low_reliability: noNoise });
    measurements.push({ metric: 'sustained_level', value: round(best.medianLevel, 2), unit: 'dBFS', low_reliability: noNoise });
  } else if (input.trials.length) {
    notes.push('"아" 녹음에서 발성을 찾지 못했다');
  }

  let f0MedianHz: number | null = null;
  if (input.sentence) {
    const sp = sentencePitch(input.sentence.levels, input.sentence.f0, floorFor(input.sentence.levels));
    if (sp) {
      f0MedianHz = sp.f0MedianHz;
      measurements.push({ metric: 'f0_median', value: round(sp.f0MedianHz, 1), unit: 'Hz', low_reliability: sp.lowReliability || noNoise });
      if (sp.lowReliability) notes.push(`평가 문장의 유성 프레임 비율 ${Math.round(sp.voicedRatio * 100)}%로 50% 미만`);
    } else {
      notes.push('평가 문장에서 유성 구간을 찾지 못했다');
    }
  }

  const range = input.calibration ?? input.storedCalibration;
  return {
    measurements,
    baselines: {
      noiseFloorDbfs,
      sustainedLevelDbfs: best ? best.medianLevel : null,
      f0MedianHz,
      maxPhonationTimeS: best ? best.durationS : null,
      f0RangeHz: range,
      f0SearchRangeHz: searchRange(range),
    },
    notes,
  };
}
