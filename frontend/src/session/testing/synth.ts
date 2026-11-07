// 정답을 아는 합성 신호(테스트·통합 시험용). 실제 녹음과 같은 형식: 모노, 48 kHz, 16비트 PCM WAV로 만든다.
// 낭독 합성은 backend/fixtures/synthetic/generate.py와 같은 방식(펄스열 → 성문 기울기 → 포먼트 공진기 3개)을 줄여 옮겼다.

export const RATE = 48000;

/** 시드가 같으면 같은 수열을 내는 난수(mulberry32) */
export function random(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const samplesOf = (seconds: number, rate = RATE) => Math.round(seconds * rate);
export const dbToAmp = (db: number) => 10 ** (db / 20);

function rms(x: Float32Array, from = 0, to = x.length): number {
  let s = 0;
  for (let i = from; i < to; i++) s += x[i] * x[i];
  return Math.sqrt(s / Math.max(1, to - from));
}

/** 가우스 잡음. 제곱평균제곱근 진폭이 levelDbfs가 되도록 맞춘다 */
export function noise(length: number, levelDbfs: number, seed = 1): Float32Array {
  const next = random(seed);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 2) {
    const u = Math.max(next(), 1e-12);
    const v = next();
    const r = Math.sqrt(-2 * Math.log(u));
    out[i] = r * Math.cos(2 * Math.PI * v);
    if (i + 1 < length) out[i + 1] = r * Math.sin(2 * Math.PI * v);
  }
  const scale = dbToAmp(levelDbfs) / rms(out);
  for (let i = 0; i < length; i++) out[i] *= scale;
  return out;
}

/** 배음 진폭이 1/k인 소리. f0(표본마다 기본주파수, Hz), 제곱평균제곱근 진폭이 levelDbfs. 앞뒤 20 ms는 서서히 */
export function harmonic(f0: Float32Array | number, length: number, levelDbfs: number, rate = RATE): Float32Array {
  const hz = typeof f0 === 'number' ? new Float32Array(length).fill(f0) : f0;
  let top = 0;
  for (let i = 0; i < length; i++) top = Math.max(top, hz[i]);
  const K = Math.max(1, Math.floor(4000 / top));
  const out = new Float32Array(length);
  let phase = 0;
  for (let i = 0; i < length; i++) {
    phase += (2 * Math.PI * hz[i]) / rate;
    let y = 0;
    for (let k = 1; k <= K; k++) y += Math.sin(k * phase) / k;
    out[i] = y;
  }
  const scale = dbToAmp(levelDbfs) / rms(out);
  const ramp = samplesOf(0.02, rate);
  for (let i = 0; i < length; i++) {
    const edge = Math.min(1, i / ramp, (length - 1 - i) / ramp);
    out[i] *= scale * (0.5 - 0.5 * Math.cos(Math.PI * Math.max(0, edge)));
  }
  return out;
}

/** 기본주파수가 start에서 end로 지수적으로(반음 기준 일정하게) 오르는 표본별 값 */
export function glideHz(startHz: number, endHz: number, length: number): Float32Array {
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) out[i] = startHz * (endHz / startHz) ** (i / Math.max(1, length - 1));
  return out;
}

/** 배경 잡음 위에 소리를 offset 표본부터 더한다 */
export function place(background: Float32Array, sound: Float32Array, offset: number): Float32Array {
  const out = background.slice();
  for (let i = 0; i < sound.length && offset + i < out.length; i++) out[offset + i] += sound[i];
  return out;
}

const VOWELS: Record<string, [number, number, number]> = {
  a: [750, 1250, 2600],
  e: [480, 1900, 2600],
  i: [300, 2300, 3000],
  o: [480, 850, 2500],
  u: [330, 900, 2300],
};
const BANDWIDTHS = [90, 110, 170];
const VOWEL_ORDER = ['a', 'i', 'u', 'e', 'o', 'a', 'e'];

export interface SyllableTruth {
  /** 자음 시작(초) */
  start: number;
  /** 모음 시작(초) */
  vowelStart: number;
  end: number;
}

export interface SpeechSpec {
  /** 음절 수 */
  syllables: number;
  /** 이 음절 다음에 휴지를 둔다(0부터 센 번호) */
  pauseAfter: number[];
  pauseS: number;
  /** 모음 길이(초). 음절마다 돌아가며 씀 */
  vowelS: number[];
  burstS: number;
  /** 낭독 기본주파수: 처음 → 끝(Hz, 직선) */
  f0StartHz: number;
  f0EndHz: number;
  levelDbfs: number;
  leadS: number;
  tailS: number;
  noiseDbfs: number;
  seed?: number;
}

export const S1_SPEECH: SpeechSpec = {
  syllables: 21,
  pauseAfter: [9],
  pauseS: 0.4,
  vowelS: [0.17, 0.15, 0.19, 0.16, 0.18, 0.15, 0.2],
  burstS: 0.025,
  f0StartHz: 150,
  f0EndHz: 115,
  levelDbfs: -22,
  leadS: 0.6,
  tailS: 0.8,
  noiseDbfs: -60,
};

/** 포먼트 음절로 만든 낭독. 표본과 음절 시각(정답)을 돌려준다 */
export function speech(spec: SpeechSpec, rate = RATE): { samples: Float32Array; syllables: SyllableTruth[]; f0: Float32Array } {
  const syllables: SyllableTruth[] = [];
  let t = spec.leadS;
  for (let k = 0; k < spec.syllables; k++) {
    const vowelS = spec.vowelS[k % spec.vowelS.length];
    syllables.push({ start: t, vowelStart: t + spec.burstS, end: t + spec.burstS + vowelS });
    t += spec.burstS + vowelS;
    if (spec.pauseAfter.includes(k)) t += spec.pauseS;
  }
  const length = samplesOf(t + spec.tailS, rate);
  const first = syllables[0].start;
  const last = syllables[syllables.length - 1].end;
  const f0 = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const u = Math.min(1, Math.max(0, (i / rate - first) / (last - first)));
    f0[i] = spec.f0StartHz + (spec.f0EndHz - spec.f0StartHz) * u;
  }
  const voice = new Float32Array(length);
  const next = random((spec.seed ?? 7) + 1);
  let vowelPower = 0;
  let vowelCount = 0;
  syllables.forEach((s, k) => {
    const v0 = samplesOf(s.vowelStart, rate);
    const v1 = samplesOf(s.end, rate);
    const formants = VOWELS[VOWEL_ORDER[k % VOWEL_ORDER.length]];
    // 펄스열(주기마다 1) → 성문 기울기 1 ÷ (1 − 0.95 z⁻¹)
    let phase = 0;
    let prevFloor = 0;
    let tilt = 0;
    let y = new Float32Array(v1 - v0);
    for (let i = 0; i < y.length; i++) {
      phase += f0[v0 + i] / rate;
      const pulse = Math.floor(phase) !== prevFloor ? 1 : 0;
      prevFloor = Math.floor(phase);
      tilt = pulse + 0.95 * tilt;
      y[i] = tilt;
    }
    // 포먼트 공진기: r = e^(−π × W(대역폭) ÷ fs), θ = 2π × F(중심 주파수) ÷ fs
    for (let f = 0; f < 3; f++) {
      const r = Math.exp((-Math.PI * BANDWIDTHS[f]) / rate);
      const c = 2 * r * Math.cos((2 * Math.PI * formants[f]) / rate);
      const out = new Float32Array(y.length);
      let y1 = 0;
      let y2 = 0;
      for (let i = 0; i < y.length; i++) {
        const v = (1 - r) * y[i] + c * y1 - r * r * y2;
        y2 = y1;
        y1 = v;
        out[i] = v;
      }
      y = out;
    }
    const attack = samplesOf(0.02, rate);
    const release = samplesOf(0.04, rate);
    for (let i = 0; i < y.length; i++) {
      y[i] *= Math.min(1, i / attack, (y.length - i) / release);
      voice[v0 + i] = y[i];
      vowelPower += y[i] * y[i];
      vowelCount += 1;
    }
    // 자음: 고역 잡음(이웃 표본 차이) 짧게
    const b0 = samplesOf(s.start, rate);
    let peak = 0;
    for (let i = 0; i < y.length; i++) peak = Math.max(peak, Math.abs(y[i]));
    let prev = 0;
    for (let i = b0; i < v0; i++) {
      const w = next() * 2 - 1;
      voice[i] = 0.3 * peak * (w - prev) * 0.5;
      prev = w;
    }
  });
  const scale = dbToAmp(spec.levelDbfs) / Math.sqrt(vowelPower / Math.max(1, vowelCount));
  const background = noise(length, spec.noiseDbfs, spec.seed ?? 7);
  const samples = new Float32Array(length);
  for (let i = 0; i < length; i++) samples[i] = background[i] + voice[i] * scale;
  return { samples, syllables, f0 };
}

/** 측정 블록 한 회차 녹음(정답값과 함께). 모두 48 kHz */
export function measurementBlock(seed = 3) {
  const nf = -60;
  const bg = (seconds: number, s: number) => noise(samplesOf(seconds), nf, seed * 100 + s);
  const glideLen = samplesOf(5);
  const glide = place(bg(6, 1), harmonic(glideHz(110, 330, glideLen), glideLen, -22), samplesOf(0.5));
  const noiseFloor = bg(2.5, 2);
  const trialS = [6, 7.5, 7];
  const trials = trialS.map((d, k) => place(bg(d + 1.5, 3 + k), harmonic(150, samplesOf(d), -20), samplesOf(0.5)));
  const sentence = speech({ ...S1_SPEECH, noiseDbfs: nf, seed: seed * 100 + 9 });
  return {
    glide,
    noiseFloor,
    trials,
    sentence: sentence.samples,
    truth: {
      noiseFloorDbfs: nf,
      maxPhonationTimeS: Math.max(...trialS),
      sustainedLevelDbfs: -20,
      glide: { startHz: 110, endHz: 330 },
      sentenceF0Hz: [S1_SPEECH.f0StartHz, S1_SPEECH.f0EndHz],
      syllables: sentence.syllables,
    },
  };
}
