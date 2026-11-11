// 말소리 대역 필터: 60 Hz 고역 통과 + 8 kHz 저역 통과(각각 4차 버터워스).
// 서버는 16 kHz로 바꾼 뒤(8 kHz 위가 빠짐) 60 Hz 고역 통과를 앞뒤로 두 번 건다(metrics-spec.md 4.6).
// 기기도 같은 대역에서 음량을 재야 잡음 바닥과 발성 기준(잡음 바닥 + 10 dB)이 서버와 맞는다.

/** 2차 구간 하나: y[n] = b0·x[n] + b1·x[n−1] + b2·x[n−2] − a1·y[n−1] − a2·y[n−2] (a0 = 1로 나눔) */
export interface Biquad {
  b0: number;
  b1: number;
  b2: number;
  a1: number;
  a2: number;
}

export const HIGHPASS_HZ = 60;
export const LOWPASS_HZ = 8000;
/** 4차 버터워스를 2차 구간 둘로 나눌 때의 Q */
const BUTTERWORTH_Q4 = [0.5411961, 1.3065630];

function section(kind: 'lowpass' | 'highpass', cutoffHz: number, sampleRate: number, q: number): Biquad {
  const w0 = (2 * Math.PI * cutoffHz) / sampleRate;
  const cos = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * q);
  const a0 = 1 + alpha;
  const b0 = kind === 'lowpass' ? (1 - cos) / 2 : (1 + cos) / 2;
  const b1 = kind === 'lowpass' ? 1 - cos : -(1 + cos);
  return { b0: b0 / a0, b1: b1 / a0, b2: b0 / a0, a1: (-2 * cos) / a0, a2: (1 - alpha) / a0 };
}

/** 말소리 대역 필터의 2차 구간 4개(저역 통과 2, 고역 통과 2). 저역 통과 차단 주파수가 나이퀴스트 이상이면 고역 통과만 */
export function voiceBandSections(sampleRate: number): Biquad[] {
  const out: Biquad[] = [];
  if (LOWPASS_HZ < sampleRate / 2) for (const q of BUTTERWORTH_Q4) out.push(section('lowpass', LOWPASS_HZ, sampleRate, q));
  for (const q of BUTTERWORTH_Q4) out.push(section('highpass', HIGHPASS_HZ, sampleRate, q));
  return out;
}

/** 대역 통과(lowHz~highHz): 4차 버터워스 고역 통과 + 4차 버터워스 저역 통과. 음절 핵 검출의 말소리 대역(300~2500 Hz)에 쓴다 */
export function bandSections(lowHz: number, highHz: number, sampleRate: number): Biquad[] {
  const out: Biquad[] = [];
  for (const q of BUTTERWORTH_Q4) out.push(section('highpass', lowHz, sampleRate, q));
  if (highHz < sampleRate / 2) for (const q of BUTTERWORTH_Q4) out.push(section('lowpass', highHz, sampleRate, q));
  return out;
}

function runSections(x: Float64Array, sections: Biquad[]): void {
  for (const s of sections) {
    let x1 = 0;
    let x2 = 0;
    let y1 = 0;
    let y2 = 0;
    for (let i = 0; i < x.length; i++) {
      const x0 = x[i];
      const y0 = s.b0 * x0 + s.b1 * x1 + s.b2 * x2 - s.a1 * y1 - s.a2 * y2;
      x2 = x1;
      x1 = x0;
      y2 = y1;
      y1 = y0;
      x[i] = y0;
    }
  }
}

/**
 * 녹음 전체에 말소리 대역 필터를 앞으로 한 번, 뒤로 한 번 건다(위상 변화 없음, 서버와 같은 방식)
 * 양 끝은 50 ms를 홀수 대칭으로 늘려 붙여 시작 부분의 흔들림이 녹음에 들어가지 않게 한다
 */
export function voiceBand(samples: Float32Array, sampleRate: number): Float32Array {
  return zeroPhase(samples, voiceBandSections(sampleRate), sampleRate);
}

/** 주어진 2차 구간들을 앞으로 한 번, 뒤로 한 번 건다(위상 변화 없음). 양 끝은 50 ms 홀수 대칭으로 늘려 붙인다 */
export function zeroPhase(samples: Float32Array, sections: Biquad[], sampleRate: number): Float32Array {
  const n = samples.length;
  if (n < 3) return samples.slice();
  const pad = Math.min(n - 1, Math.round(0.05 * sampleRate));
  const x = new Float64Array(n + 2 * pad);
  for (let i = 0; i < pad; i++) x[i] = 2 * samples[0] - samples[pad - i];
  for (let i = 0; i < n; i++) x[pad + i] = samples[i];
  for (let i = 0; i < pad; i++) x[pad + n + i] = 2 * samples[n - 1] - samples[n - 2 - i];
  runSections(x, sections);
  x.reverse();
  runSections(x, sections);
  x.reverse();
  return Float32Array.from(x.subarray(pad, pad + n));
}

/** 실시간용 2차 구간 묶음(앞으로 한 번). 버퍼가 이어서 들어와도 상태를 이어 간다 */
export class BiquadChain {
  private readonly sections: Biquad[];
  private readonly state: Float64Array;

  constructor(sections: Biquad[]) {
    this.sections = sections;
    this.state = new Float64Array(this.sections.length * 4);
  }

  process(input: ArrayLike<number>): Float32Array {
    const out = new Float32Array(input.length);
    for (let i = 0; i < input.length; i++) {
      let v = input[i];
      for (let k = 0; k < this.sections.length; k++) {
        const s = this.sections[k];
        const o = k * 4;
        const y = s.b0 * v + s.b1 * this.state[o] + s.b2 * this.state[o + 1] - s.a1 * this.state[o + 2] - s.a2 * this.state[o + 3];
        this.state[o + 1] = this.state[o];
        this.state[o] = v;
        this.state[o + 3] = this.state[o + 2];
        this.state[o + 2] = y;
        v = y;
      }
      out[i] = v;
    }
    return out;
  }

  reset(): void {
    this.state.fill(0);
  }
}

/**
 * 실시간용 말소리 대역 필터(앞으로 한 번). 버퍼가 이어서 들어와도 상태를 이어 간다
 * 측정 블록의 자동 멈춤과 게임의 실시간 음량은 이 필터를 거친 뒤 잰다
 */
export class VoiceBandFilter extends BiquadChain {
  constructor(sampleRate: number) {
    super(voiceBandSections(sampleRate));
  }
}

/** 버퍼 하나의 음량(dBFS) = 20 × log₁₀(RMS(제곱평균제곱근 진폭) + 10⁻¹⁰) */
export function bufferLevelDbfs(x: ArrayLike<number>): number {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i] * x[i];
  return 20 * Math.log10(Math.sqrt(s / Math.max(1, x.length)) + 1e-10);
}
