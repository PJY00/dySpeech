// 문장을 끝내는 조건(순수 함수). 숫자는 모두 초기값이다.
// - 실시간 추정이 N(정답 음절 수)의 80% 이상: 1초 조용하면 끝(안내 커서와 관계없음)
// - 80% 미만: 안내 커서가 끝난 뒤 3초 조용하면 끝. 커서가 끝나기 전에는 조용해도 끝내지 않는다
//   (문장 중간에 숨 쉬느라 쉬어도 녹음이 잘리지 않게)
// - 최대 시간(maxS)이 되면 끝

/** 이만큼 읽었다고 추정되면 짧은 무음으로 끝낸다 */
export const END_PROGRESS_RATIO = 0.8;
/** 80% 이상일 때 끝내는 무음 길이(초) */
export const EARLY_END_SILENCE_S = 1;
/** 80% 미만일 때 안내 커서가 끝난 뒤 기다리는 무음 길이(초) */
export const LATE_END_SILENCE_S = 3;
/** 소리로 보는 기준: 잡음 바닥 + 10 dB. 잡음 바닥이 없으면 −60 dBFS로 본다 */
const VOICE_MARGIN_DB = 10;
const FALLBACK_FLOOR_DBFS = -60;

export type EndReason = 'progress' | 'after_guide' | 'max';

export interface SentenceEndOptions {
  syllables: number;
  /** 잡음 바닥(dBFS, 60 Hz~8 kHz). 없으면 null */
  floorDbfs: number | null;
  /** 녹음 시작부터 안내 커서가 움직이기 시작할 때까지(초) */
  guideStartS: number;
  /** 안내 커서가 문장 끝까지 가는 시간(초) */
  guideS: number;
  maxS: number;
}

export class SentenceEnd {
  private readonly o: SentenceEndOptions;
  private readonly floor: number;
  private elapsed = 0;
  private quiet = 0;
  private heard = false;
  reason: EndReason | null = null;

  constructor(options: SentenceEndOptions) {
    this.o = options;
    this.floor = options.floorDbfs ?? FALLBACK_FLOOR_DBFS;
  }

  /** 버퍼 하나(음량 dBFS, 길이 dtS초)와 지금까지의 실시간 추정(음절)을 넣고, 끝낼 때면 참 */
  push(levelDbfs: number, dtS: number, estimate: number): boolean {
    if (this.reason) return true;
    this.elapsed += dtS;
    if (levelDbfs > this.floor + VOICE_MARGIN_DB) {
      this.heard = true;
      this.quiet = 0;
    } else {
      this.quiet += dtS;
    }
    if (this.elapsed >= this.o.maxS) this.reason = 'max';
    else if (estimate >= END_PROGRESS_RATIO * this.o.syllables) {
      if (this.heard && this.quiet >= EARLY_END_SILENCE_S) this.reason = 'progress';
    } else {
      const sinceGuideEnd = this.elapsed - (this.o.guideStartS + this.o.guideS);
      if (sinceGuideEnd >= 0 && Math.min(this.quiet, sinceGuideEnd) >= LATE_END_SILENCE_S) this.reason = 'after_guide';
    }
    return this.reason !== null;
  }

  get elapsedS(): number {
    return this.elapsed;
  }
}
