// 문장 하나의 시간 배치(순수 함수). 숫자는 모두 초기값이다.
// - [Start]를 누르면 녹음을 시작하고 3·2·1(COUNTDOWN_S초, 1초씩)을 보여 준 뒤 안내 커서가 움직인다. 문장은 그동안 계속 보인다
// - 녹음은 [Start]부터 하지만, 판정(말속도·변동계수)과 실시간 진행 표시에는 커서 시작 0.5초 전부터의 소리만 쓴다.
//   카운트다운 동안 소리 내어 미리 읽어 본 것이 말속도와 "다 읽었다" 추정에 섞이지 않게 하기 위해서다
// - 문장을 끝내는 최대 시간 = 준비 시간(카운트다운) + 커서 시간 × 2.5 + 4초

/** [Start]부터 안내 커서가 움직이기 전까지(초). 이 동안 3·2·1을 1초씩 보여 준다 */
export const COUNTDOWN_S = 3;
/** 판정에 쓰는 소리의 시작: 커서 시작보다 이만큼 앞(초). 커서보다 조금 일찍 읽기 시작해도 앞부분이 잘리지 않게 */
export const JUDGE_LEAD_S = 0.5;
/** 최대 시간 = 준비 시간 + 커서 시간 × MAX_GUIDE_FACTOR + MAX_EXTRA_S */
const MAX_GUIDE_FACTOR = 2.5;
const MAX_EXTRA_S = 4;

export interface SentenceTiming {
  /** 녹음 시작부터 안내 커서가 움직이기 시작할 때까지(초) */
  guideStartS: number;
  /** 안내 커서가 문장 끝까지 가는 시간(초) = N(음절 수) ÷ 안내 속도(음절/초) */
  guideS: number;
  /** 문장을 끝내는 최대 시간(초, 녹음 시작부터) */
  maxS: number;
  /** 판정에 쓰는 소리의 시작(초, 녹음 시작부터) */
  judgeFromS: number;
}

export function sentenceTiming(syllables: number, guideRate: number): SentenceTiming {
  const guideS = syllables / guideRate;
  return {
    guideStartS: COUNTDOWN_S,
    guideS,
    maxS: COUNTDOWN_S + guideS * MAX_GUIDE_FACTOR + MAX_EXTRA_S,
    judgeFromS: COUNTDOWN_S - JUDGE_LEAD_S,
  };
}

/** 녹음 파일 표본에서 판정 시작(fromS초) 앞부분을 뺀 것 */
export function judgedSamples(samples: Float32Array, sampleRate: number, fromS: number): Float32Array {
  const start = Math.min(samples.length, Math.max(0, Math.round(fromS * sampleRate)));
  return samples.subarray(start);
}

/** 마이크 버퍼를 차례로 받아 판정 시작(fromS초) 뒤 부분만 돌려준다. 시작 시각을 걸친 버퍼는 뒤쪽만 */
export class JudgeGate {
  private readonly from: number;
  private seen = 0;

  constructor(fromS: number, sampleRate: number) {
    this.from = Math.max(0, Math.round(fromS * sampleRate));
  }

  take(chunk: Float32Array): Float32Array | null {
    const start = this.seen;
    this.seen += chunk.length;
    if (this.seen <= this.from) return null;
    return start >= this.from ? chunk : chunk.subarray(this.from - start);
  }
}
