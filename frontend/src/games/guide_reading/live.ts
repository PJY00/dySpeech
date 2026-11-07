// 실시간 진행 표시: 마이크 버퍼(onAudioReady)를 받아 지금까지 읽은 음절 수를 추정한다.
// 음절 핵 검출만 쓰면 놓친 음절에서 진행 표시가 멈추므로, 두 값을 섞는다.
//   a(검출 기반) = 검출한 음절 핵 수 ÷ r(사용자별 검출 비율)
//   b(시간 기반) = 지금까지 발화 시간(초) × R(기준 말속도, 음절/초)
//   추정 = max(이전 추정, 0.7 × a + 0.3 × b). 문장을 끝내기 전에는 N − 0.5를 넘지 않는다

import { BiquadChain, bandSections, voiceBandSections } from '../../measurement/filters';
import { DEFAULT_RANGE_HZ, percentile } from '../../measurement/pitch';
import { PitchDetector } from 'pitchy';
import type { HzRange } from '../../session/types';
import { NUCLEUS_BAND_HZ } from './analysis';
import { NucleusDetector } from './nuclei';

const FRAME_S = 0.03;
const HOP_S = 0.01;
const PITCH_WINDOW_AT_48K = 2048;
const CLARITY_MIN = 0.85;
const VOICE_MARGIN_DB = 10;
/** 잡음 바닥을 정하기 전에 모으는 프레임 수(0.3초, 준비 시간 안) */
const FLOOR_FRAMES = 30;
const DETECTED_WEIGHT = 0.7;

export interface LiveReaderOptions {
  sampleRate: number;
  syllables: number;
  referenceRate: number;
  detectionRatio: number;
  /** 측정 블록 잡음 바닥(60 Hz~8 kHz). 없으면 처음 0.3초로 정한다 */
  noiseFloorDbfs: number | null;
  searchRange?: HzRange;
}

export class LiveReader {
  private readonly o: Required<Omit<LiveReaderOptions, 'noiseFloorDbfs'>> & { noiseFloorDbfs: number | null };
  private readonly voiceFilter: BiquadChain;
  private readonly bandFilter: BiquadChain;
  private readonly pitch: PitchDetector<Float32Array>;
  private readonly hop: number;
  private readonly frame: number;
  private readonly window: number;
  private voice = new Float32Array(0);
  private band = new Float32Array(0);
  private filled = 0;
  private nextFrame = 0;
  private readonly pending: { voiceLevel: number; bandLevel: number; voiced: boolean }[] = [];
  private voiceFloor: number | null;
  private detector: NucleusDetector | null = null;
  private speechFrames = 0;
  private estimateValue = 0;
  private finished = false;

  constructor(options: LiveReaderOptions) {
    this.o = { searchRange: DEFAULT_RANGE_HZ, ...options };
    this.voiceFilter = new BiquadChain(voiceBandSections(options.sampleRate));
    this.bandFilter = new BiquadChain(bandSections(NUCLEUS_BAND_HZ.min, NUCLEUS_BAND_HZ.max, options.sampleRate));
    this.hop = Math.round(HOP_S * options.sampleRate);
    this.frame = Math.round(FRAME_S * options.sampleRate);
    this.window = Math.round((PITCH_WINDOW_AT_48K * options.sampleRate) / 48000);
    this.pitch = PitchDetector.forFloat32Array(this.window);
    this.voiceFloor = options.noiseFloorDbfs;
  }

  /** 마이크 버퍼 하나(−1~1) */
  push(chunk: ArrayLike<number>): void {
    const v = this.voiceFilter.process(chunk);
    const b = this.bandFilter.process(chunk);
    this.append(v, b);
    // 프레임 중심 기준 기본주파수 창(약 43 ms)이 다 들어온 프레임까지 처리
    for (;;) {
      const start = this.nextFrame * this.hop;
      const center = start + this.frame / 2;
      const end = Math.max(start + this.frame, Math.round(center + this.window / 2));
      if (end > this.filled) break;
      this.processFrame(start, center);
      this.nextFrame += 1;
    }
  }

  /** 지금까지 읽은 음절 수 추정(0~N). 줄지 않는다 */
  get estimate(): number {
    return this.estimateValue;
  }

  get detected(): number {
    return this.detector?.peaks.length ?? 0;
  }

  /** 지금까지 발화 시간(초) */
  get speechS(): number {
    return this.speechFrames * HOP_S;
  }

  /** 문장을 끝냈을 때: 진행 표시를 끝까지 채운다 */
  finish(): void {
    this.finished = true;
    this.estimateValue = this.o.syllables;
  }

  private append(v: Float32Array, b: Float32Array): void {
    if (this.filled + v.length > this.voice.length) {
      const size = Math.max(this.voice.length * 2, this.filled + v.length, this.o.sampleRate);
      const nv = new Float32Array(size);
      const nb = new Float32Array(size);
      nv.set(this.voice.subarray(0, this.filled));
      nb.set(this.band.subarray(0, this.filled));
      this.voice = nv;
      this.band = nb;
    }
    this.voice.set(v, this.filled);
    this.band.set(b, this.filled);
    this.filled += v.length;
  }

  private level(x: Float32Array, start: number): number {
    let s = 0;
    for (let i = start; i < start + this.frame; i++) s += x[i] * x[i];
    return 20 * Math.log10(Math.sqrt(s / this.frame) + 1e-10);
  }

  private processFrame(start: number, center: number): void {
    const voiceLevel = this.level(this.voice, start);
    const bandLevel = this.level(this.band, start);
    const floor = this.voiceFloor;
    let voiced = false;
    if (floor === null || voiceLevel > floor + VOICE_MARGIN_DB) {
      const w0 = Math.round(center - this.window / 2);
      if (w0 >= 0) {
        const [hz, clarity] = this.pitch.findPitch(this.voice.subarray(w0, w0 + this.window), this.o.sampleRate);
        voiced = clarity >= CLARITY_MIN && hz >= this.o.searchRange.min && hz <= this.o.searchRange.max;
      }
    }
    if (!this.detector) {
      this.pending.push({ voiceLevel, bandLevel, voiced });
      if (this.pending.length < FLOOR_FRAMES) return;
      if (this.voiceFloor === null) this.voiceFloor = percentile(this.pending.map((p) => p.voiceLevel), 10);
      this.detector = new NucleusDetector(percentile(this.pending.map((p) => p.bandLevel), 10));
      for (const p of this.pending.splice(0)) this.feed(p.voiceLevel, p.bandLevel, p.voiced);
      return;
    }
    this.feed(voiceLevel, bandLevel, voiced);
  }

  private feed(voiceLevel: number, bandLevel: number, voiced: boolean): void {
    if (voiceLevel > (this.voiceFloor ?? -60) + VOICE_MARGIN_DB) this.speechFrames += 1;
    this.detector?.push(bandLevel, voiced);
    if (this.finished) return;
    const a = this.detected / Math.max(0.1, this.o.detectionRatio);
    const b = this.speechS * this.o.referenceRate;
    const blend = DETECTED_WEIGHT * a + (1 - DETECTED_WEIGHT) * b;
    this.estimateValue = Math.min(this.o.syllables - 0.5, Math.max(this.estimateValue, blend));
  }
}
