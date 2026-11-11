// 문장 녹음 하나의 분석: 말속도(서버 조음속도와 같은 정의), 음절 핵, 변동계수.
// 음량과 발화 구간은 측정 블록과 같은 대역(60 Hz~8 kHz), 음절 핵은 말소리 대역(300~2500 Hz) 음량에서 찾는다.

import { articulationRate, frameLevels, speechMask } from '../../measurement/analysis';
import { bandSections, voiceBand, zeroPhase } from '../../measurement/filters';
import { percentile, pitchTrack } from '../../measurement/pitch';
import type { HzRange } from '../../session/types';
import { detectNuclei, intervalCv } from './nuclei';
import type { SentenceMeasure } from './scoring';

/** 음절 핵 검출 대역(Hz). 실제 녹음 하나에서 60 Hz~8 kHz보다 조금 더 찾았다(21음절 중 14 대 13) */
export const NUCLEUS_BAND_HZ = { min: 300, max: 2500 };
/** 잡음 바닥이 없을 때 쓰는 녹음 프레임 음량 백분위 */
const FLOOR_PERCENTILE = 10;

export interface SentenceAnalysis extends SentenceMeasure {
  /** 음절 핵 프레임 번호(10 ms 간격) */
  peaks: number[];
  noiseFloorDbfs: number;
}

/**
 * samples: 문장 녹음(−1~1). 앞쪽 준비 시간과 끝의 무음이 들어 있다
 * noiseFloorDbfs: 측정 블록 잡음 바닥(없으면 이 녹음의 하위 10백분위)
 */
export function analyzeSentence(
  samples: Float32Array,
  sampleRate: number,
  syllables: number,
  options: { noiseFloorDbfs: number | null; searchRange: HzRange },
): SentenceAnalysis {
  const voice = voiceBand(samples, sampleRate);
  const levels = frameLevels(voice, sampleRate);
  const floor = options.noiseFloorDbfs ?? percentile(levels, FLOOR_PERCENTILE);
  const ar = articulationRate(levels, floor, syllables);
  const speech = speechMask(levels, floor);
  const band = frameLevels(zeroPhase(samples, bandSections(NUCLEUS_BAND_HZ.min, NUCLEUS_BAND_HZ.max, sampleRate), sampleRate), sampleRate);
  const bandFloor = percentile(band, FLOOR_PERCENTILE);
  const voiced = pitchTrack(voice, sampleRate, levels.length, options.searchRange, speech).map((v) => v !== null);
  const peaks = detectNuclei(band, voiced, bandFloor);
  return {
    rate: ar ? ar.rate : null,
    speechS: ar ? ar.speechS : 0,
    cv: intervalCv(peaks, speech),
    detected: peaks.length,
    peaks,
    noiseFloorDbfs: floor,
  };
}
