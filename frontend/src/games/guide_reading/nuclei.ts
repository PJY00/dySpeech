// 음절 핵 검출(진행 표시와 변동계수용). docs/metrics-spec.md 5장 기기 권장 방법을 따른다.
// - 50 ms 이동평균 음량 곡선의 정점. 정점에서 2 dB 넘게 내려가면 정점으로 확정
// - 정점 사이 최소 0.12초, 정점은 잡음 바닥 + 10 dB 위이고 유성 프레임(pitchy 명료도 0.85 이상) 근처
// 프레임은 10 ms 간격(analysis.frameLevels와 같음). 녹음 파일 분석과 실시간 진행 표시가 같은 상태 기계를 쓴다.

export interface NucleusOptions {
  /** 이동평균 프레임 수(10 ms × 5 = 50 ms) */
  smoothFrames: number;
  /** 정점 확정에 필요한 하강(dB) */
  dipDb: number;
  /** 정점 사이 최소 간격(초) */
  minGapS: number;
  /** 정점은 잡음 바닥보다 이만큼(dB) 커야 한다 */
  marginDb: number;
  /** 정점 앞뒤 이 프레임 수 안에 유성 프레임이 있어야 한다 */
  voicedReach: number;
}

export const NUCLEUS_DEFAULTS: NucleusOptions = { smoothFrames: 5, dipDb: 2, minGapS: 0.12, marginDb: 10, voicedReach: 2 };

const HOP_S = 0.01;

/** 프레임 하나씩 넣는 음절 핵 검출기. push가 정점을 확정하면 그 프레임 번호를 돌려준다 */
export class NucleusDetector {
  private readonly opts: NucleusOptions;
  private readonly floor: number;
  private readonly window: number[] = [];
  private readonly voicedHistory: boolean[] = [];
  private frame = -1;
  private candidate: { value: number; frame: number } | null = null;
  private valley = Infinity;
  private lastPeak = -Infinity;
  readonly peaks: number[] = [];

  constructor(noiseFloorDbfs: number, options: Partial<NucleusOptions> = {}) {
    this.opts = { ...NUCLEUS_DEFAULTS, ...options };
    this.floor = noiseFloorDbfs;
  }

  /** levelDbfs: 프레임 음량, voiced: 그 프레임이 유성인지 */
  push(levelDbfs: number, voiced: boolean): number | null {
    this.frame += 1;
    this.voicedHistory.push(voiced);
    this.window.push(levelDbfs);
    if (this.window.length > this.opts.smoothFrames) this.window.shift();
    const smooth = this.window.reduce((a, b) => a + b, 0) / this.window.length;
    // 이동평균의 가운데 프레임을 그 값의 시각으로 본다
    const at = this.frame - Math.floor((this.window.length - 1) / 2);

    if (!this.candidate) {
      if (smooth < this.valley) this.valley = smooth;
      if (smooth > this.valley && smooth > this.floor + this.opts.marginDb) this.candidate = { value: smooth, frame: at };
      return null;
    }
    if (smooth >= this.candidate.value) {
      this.candidate = { value: smooth, frame: at };
      return null;
    }
    if (this.candidate.value - smooth < this.opts.dipDb) return null;
    const peak = this.candidate;
    this.candidate = null;
    this.valley = smooth;
    if ((peak.frame - this.lastPeak) * HOP_S < this.opts.minGapS) return null;
    if (!this.voicedNear(peak.frame)) return null;
    this.lastPeak = peak.frame;
    this.peaks.push(peak.frame);
    return peak.frame;
  }

  private voicedNear(frame: number): boolean {
    const r = this.opts.voicedReach;
    for (let i = Math.max(0, frame - r); i <= Math.min(this.voicedHistory.length - 1, frame + r); i++) {
      if (this.voicedHistory[i]) return true;
    }
    return false;
  }
}

/** 녹음 전체의 음절 핵 프레임 번호 */
export function detectNuclei(levels: ArrayLike<number>, voiced: ArrayLike<boolean>, noiseFloorDbfs: number, options: Partial<NucleusOptions> = {}): number[] {
  const d = new NucleusDetector(noiseFloorDbfs, options);
  for (let i = 0; i < levels.length; i++) d.push(levels[i], voiced[i]);
  return d.peaks;
}

/**
 * 변동계수 = σ(음절 간격의 표준편차) ÷ μ(음절 간격의 평균). 간격은 이웃 음절 핵 사이 시간(초)
 * speech(발화 프레임 표시)를 주면 휴지(0.25초 이상 공백)를 건너는 간격은 뺀다. 말속도가 휴지를 빼는 것과 맞춘다
 * 남은 간격이 2개 미만이면 null
 */
export function intervalCv(peakFrames: number[], speech?: ArrayLike<boolean>): number | null {
  const gaps: number[] = [];
  for (let i = 1; i < peakFrames.length; i++) {
    const a = peakFrames[i - 1];
    const b = peakFrames[i];
    if (speech) {
      let pause = false;
      for (let f = a; f <= b && !pause; f++) if (!speech[f]) pause = true;
      if (pause) continue;
    }
    gaps.push((b - a) * HOP_S);
  }
  if (gaps.length < 2) return null;
  const mean = gaps.reduce((a, b) => a + b, 0) / gaps.length;
  const variance = gaps.reduce((a, g) => a + (g - mean) ** 2, 0) / gaps.length;
  return mean > 0 ? Math.sqrt(variance) / mean : null;
}
