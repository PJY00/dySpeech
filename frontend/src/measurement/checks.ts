// 녹음마다 하는 검사와 분석(순수 함수). 화면은 결과로 "다음 / 다시 하기 / 이대로 쓰기"를 고른다.

import type { HzRange } from '../session/types';
import { decodePcm16, parseWav, WavError, wavProblems } from '../session/wav';
import type { WavInfo } from '../session/wav';
import { calibrationRange, frameLevels, HOP_S, longestPhonation, median, speechMask, VOICE_MARGIN_DB } from './analysis';
import { voiceBand } from './filters';
import { DEFAULT_RANGE_HZ, pitchTrack, searchRange } from './pitch';
import { MIN_PHONATION_S, MIN_SPEECH_S, NOISY_FLOOR_DBFS } from './steps';
import type { StepId } from './steps';

export interface StepAnalysis {
  levels: Float64Array;
  /** 글라이드·문장: 보정 전 기본주파수(Hz). 그 밖은 null */
  f0: (number | null)[] | null;
  /** 글라이드: 편한 음높이 범위 */
  calibration: HzRange | null;
  durationS: number;
}

export interface StepCheck {
  /** 받아들일 수 있는지 */
  ok: boolean;
  /** WAV 규격 문제라 "이대로 쓰기"를 할 수 없음 */
  fatal: boolean;
  /** 사용자에게 보여 줄 한 줄 */
  message: string;
  analysis: StepAnalysis | null;
}

export interface CheckContext {
  /** 이번 회차 noise 단계의 잡음 바닥(dBFS). 아직 없으면 null */
  noiseFloorDbfs: number | null;
  /** 이번 회차 글라이드 또는 저장된 캘리브레이션 */
  calibration: HzRange | null;
}

export function checkRecording(step: StepId, bytes: Uint8Array, ctx: CheckContext): StepCheck {
  let info: WavInfo;
  try {
    info = parseWav(bytes);
  } catch (e) {
    return { ok: false, fatal: true, message: `녹음 파일을 읽지 못했어요: ${e instanceof WavError ? e.message : String(e)}`, analysis: null };
  }
  const problems = wavProblems(info);
  if (problems.length) return { ok: false, fatal: true, message: `녹음 파일 형식이 맞지 않아요: ${problems.join(' / ')}`, analysis: null };
  // 서버와 같은 대역(60 Hz~8 kHz)에서 음량과 기본주파수를 잰다
  const samples = voiceBand(decodePcm16(bytes, info), info.sampleRate);
  const levels = frameLevels(samples, info.sampleRate);
  const base: StepAnalysis = { levels, f0: null, calibration: null, durationS: info.durationS };
  // 잡음 바닥이 아직 없으면(글라이드) 이 녹음의 하위 10백분위를 쓴다
  const floor = ctx.noiseFloorDbfs ?? percentileOf(levels, 10);

  if (step === 'noise') {
    const nf = median(levels);
    if (nf > NOISY_FLOOR_DBFS) {
      return { ok: true, fatal: false, message: `주변이 조금 시끄러워요(${nf.toFixed(0)} dBFS). 조용한 곳이면 다시 해 주세요.`, analysis: base };
    }
    return { ok: true, fatal: false, message: '좋아요.', analysis: base };
  }

  if (step === 'glide') {
    const voiced = Array.from(levels, (l) => l > floor + VOICE_MARGIN_DB);
    const f0 = pitchTrack(samples, info.sampleRate, levels.length, DEFAULT_RANGE_HZ, voiced);
    const calibration = calibrationRange(f0);
    if (!calibration) return { ok: false, fatal: false, message: '목소리 높낮이를 찾지 못했어요. 조금 더 길게 "아~" 해 주세요.', analysis: { ...base, f0 } };
    return {
      ok: true,
      fatal: false,
      message: `좋아요. ${calibration.min.toFixed(0)}~${calibration.max.toFixed(0)} Hz`,
      analysis: { ...base, f0, calibration },
    };
  }

  if (step === 'sentence') {
    const mask = speechMask(levels, floor);
    const speechS = mask.filter(Boolean).length * HOP_S;
    if (speechS < MIN_SPEECH_S) return { ok: false, fatal: false, message: '목소리가 잘 들리지 않았어요. 조금 더 크게 읽어 주세요.', analysis: base };
    const f0 = pitchTrack(samples, info.sampleRate, levels.length, searchRange(ctx.calibration), mask);
    return { ok: true, fatal: false, message: '좋아요.', analysis: { ...base, f0 } };
  }

  // mpt1~3
  const p = longestPhonation(levels, floor);
  if (!p || p.durationS < MIN_PHONATION_S) {
    return { ok: false, fatal: false, message: '"아" 소리가 짧거나 작았어요. 편한 크기로 길게 해 주세요.', analysis: base };
  }
  return { ok: true, fatal: false, message: `좋아요. ${p.durationS.toFixed(1)}초`, analysis: base };
}

function percentileOf(values: ArrayLike<number>, p: number): number {
  const a = Array.from(values).sort((x, y) => x - y);
  if (!a.length) return -100;
  const idx = (p / 100) * (a.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return a[lo] + (a[hi] - a[lo]) * (idx - lo);
}

/**
 * 자동 멈춤: 소리(음량 > 기준 + 10 dB)가 minVoiceS 넘게 난 뒤 silenceStopS 동안 조용하면 멈춘다. 최대 시간이 되면 멈춘다
 * 기준은 잡음 바닥(없으면 −60 dBFS)
 */
export class AutoStop {
  private readonly floor: number;
  private readonly silenceStopS: number | undefined;
  private readonly minVoiceS: number;
  private readonly maxS: number;
  private voiceS = 0;
  private quietS = 0;
  private elapsedS = 0;

  constructor(options: { floorDbfs: number | null; silenceStopS?: number; minVoiceS?: number; maxS: number }) {
    this.floor = options.floorDbfs ?? -60;
    this.silenceStopS = options.silenceStopS;
    this.minVoiceS = options.minVoiceS ?? 0.3;
    this.maxS = options.maxS;
  }

  /** 버퍼 하나(levelDbfs, 길이 dtS초)를 넣고, 멈출 때면 참 */
  push(levelDbfs: number, dtS: number): boolean {
    this.elapsedS += dtS;
    if (levelDbfs > this.floor + VOICE_MARGIN_DB) {
      this.voiceS += dtS;
      this.quietS = 0;
    } else {
      this.quietS += dtS;
    }
    if (this.elapsedS >= this.maxS) return true;
    return this.silenceStopS !== undefined && this.voiceS >= this.minVoiceS && this.quietS >= this.silenceStopS;
  }

  get elapsed(): number {
    return this.elapsedS;
  }
}
