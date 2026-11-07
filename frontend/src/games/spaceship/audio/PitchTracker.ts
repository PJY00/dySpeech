import { PitchDetector } from 'pitchy';
import { hzToSt, median } from './pitchMath';

export interface PitchFrame {
  time: number;
  levelDbfs: number;
  hz: number | null;
  rawHz: number | null;
  clarity: number;
}

export interface PitchTrackerOptions {
  minHz?: number;
  maxHz?: number;
}

export const WINDOW_SEC = 2048 / 48000;
export const HOP_SEC = 0.01;
export const LEVEL_SEC = 0.03;
export const CLARITY_THRESHOLD = 0.85;
export const MEDIAN_FRAMES = 5;
export const DEFAULT_MIN_HZ = 75;
export const DEFAULT_MAX_HZ = 500;

const EPS = 1e-10;

export function levelDbfs(buf: ArrayLike<number>, from = 0, to = buf.length): number {
  let s = 0;
  for (let i = from; i < to; i++) s += buf[i] * buf[i];
  const n = to - from;
  return 20 * Math.log10((n ? Math.sqrt(s / n) : 0) + EPS);
}

export class PitchTracker {
  readonly sampleRate: number;
  readonly windowSize: number;
  readonly hopSize: number;
  readonly minHz: number;
  readonly maxHz: number;
  private detector: PitchDetector<Float32Array>;
  private buf: Float32Array;
  private filled = 0;
  private consumed = 0;
  private recent: { hz: number; time: number }[] = [];

  constructor(sampleRate: number, options: PitchTrackerOptions = {}) {
    this.sampleRate = sampleRate;
    this.windowSize = Math.round(WINDOW_SEC * sampleRate);
    this.hopSize = Math.max(1, Math.round(HOP_SEC * sampleRate));
    this.minHz = options.minHz ?? DEFAULT_MIN_HZ;
    this.maxHz = options.maxHz ?? DEFAULT_MAX_HZ;
    this.detector = PitchDetector.forFloat32Array(this.windowSize);
    this.detector.clarityThreshold = 0.8;
    this.buf = new Float32Array(this.windowSize * 4);
  }

  push(chunk: ArrayLike<number>): PitchFrame[] {
    if (this.filled + chunk.length > this.buf.length) {
      const bigger = new Float32Array((this.filled + chunk.length) * 2);
      bigger.set(this.buf.subarray(0, this.filled));
      this.buf = bigger;
    }
    this.buf.set(chunk, this.filled);
    this.filled += chunk.length;

    const out: PitchFrame[] = [];
    let offset = 0;
    while (this.filled - offset >= this.windowSize) {
      out.push(this.analyze(this.buf.subarray(offset, offset + this.windowSize), this.consumed + offset));
      offset += this.hopSize;
    }
    if (offset > 0) {
      this.buf.copyWithin(0, offset, this.filled);
      this.filled -= offset;
      this.consumed += offset;
    }
    return out;
  }

  private analyze(win: Float32Array, startSample: number): PitchFrame {
    const time = (startSample + this.windowSize / 2) / this.sampleRate;
    const half = Math.round((LEVEL_SEC * this.sampleRate) / 2);
    const mid = Math.floor(win.length / 2);
    const level = levelDbfs(win, Math.max(0, mid - half), Math.min(win.length, mid + half));

    let rawHz: number | null = null;
    let clarity = 0;
    if (level > -90) {
      const [p, c] = this.detector.findPitch(win, this.sampleRate);
      clarity = c;
      if (c >= CLARITY_THRESHOLD && p >= this.minHz && p <= this.maxHz) rawHz = p;
    }
    return { time, levelDbfs: level, rawHz, hz: this.smooth(rawHz, time), clarity };
  }

  private smooth(rawHz: number | null, time: number): number | null {
    this.recent = this.recent.filter((r) => time - r.time <= 0.1);
    if (rawHz === null) return null;
    let hz = rawHz;
    if (this.recent.length) {
      const ref = median(this.recent.map((r) => r.hz));
      const jump = hzToSt(hz, ref);
      if (Math.abs(jump - 12) <= 3) hz /= 2;
      else if (Math.abs(jump + 12) <= 3) hz *= 2;
      if (hz < this.minHz || hz > this.maxHz) hz = rawHz;
    }
    this.recent.push({ hz, time });
    if (this.recent.length > MEDIAN_FRAMES) this.recent.shift();
    return median(this.recent.map((r) => r.hz));
  }
}
