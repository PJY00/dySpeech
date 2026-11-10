// 측정 블록 단계와 시간 값. 숫자는 모두 초기값이다.

export const EVAL_SENTENCE = {
  id: 's1',
  text: '우리는 저녁마다 노래를 부르며 마을을 걸었습니다',
  syllables: 21,
} as const;

export type StepId = 'glide' | 'noise' | 'mpt1' | 'mpt2' | 'mpt3' | 'sentence';

export interface StepSpec {
  id: StepId;
  title: string;
  guide: string;
  /** 정해진 시간만 녹음(초). 없으면 사용자가 멈추거나 자동 멈춤 */
  fixedS?: number;
  /** 최대 녹음 시간(초) */
  maxS: number;
  /** 소리가 난 뒤 이만큼(초) 조용하면 자동으로 멈춤. 없으면 자동 멈춤 없음 */
  silenceStopS?: number;
  /** 자동 멈춤 전에 필요한 소리 길이(초) */
  minVoiceS?: number;
}

export const STEP_SPECS: Record<StepId, StepSpec> = {
  glide: {
    id: 'glide',
    title: '목소리 높낮이 재기',
    guide: '낮은 소리에서 높은 소리로, 미끄러지듯 "아~" 해 주세요.',
    maxS: 10,
    silenceStopS: 1.5,
    minVoiceS: 1,
  },
  noise: {
    id: 'noise',
    title: '조용히 있기',
    guide: '2초 동안 아무 소리도 내지 말고 조용히 있어 주세요.',
    fixedS: 2.5,
    maxS: 2.5,
  },
  mpt1: { id: 'mpt1', title: '"아" 길게 1번째', guide: '편한 크기로 "아~"를 최대한 길게 해 주세요.', maxS: 60, silenceStopS: 1.5, minVoiceS: 0.3 },
  mpt2: { id: 'mpt2', title: '"아" 길게 2번째', guide: '한 번 더, 편한 크기로 "아~"를 최대한 길게 해 주세요.', maxS: 60, silenceStopS: 1.5, minVoiceS: 0.3 },
  mpt3: { id: 'mpt3', title: '"아" 길게 3번째', guide: '마지막으로 "아~"를 최대한 길게 해 주세요. 힘들면 건너뛰어도 돼요.', maxS: 60, silenceStopS: 1.5, minVoiceS: 0.3 },
  sentence: {
    id: 'sentence',
    title: '문장 읽기',
    guide: '아래 문장을 평소처럼 읽어 주세요.',
    maxS: 30,
    silenceStopS: 2,
    minVoiceS: 1,
  },
};

export const DEFAULT_TRIALS = 3;
/** "아"는 최소 2회 */
export const MIN_TRIALS = 2;
/** "아" 녹음으로 받아들이는 최소 발성 길이(초) */
export const MIN_PHONATION_S = 1;
/** 문장 녹음으로 받아들이는 최소 발화 길이(초) */
export const MIN_SPEECH_S = 1;
/** 잡음 바닥이 이보다 크면(dBFS) 주변이 시끄럽다고 알린다 */
export const NOISY_FLOOR_DBFS = -40;

/** 첫 회차(캘리브레이션 필요)면 글라이드를 앞에 둔다 */
export function blockSteps(needsCalibration: boolean, trials = DEFAULT_TRIALS): StepId[] {
  const mpt: StepId[] = (['mpt1', 'mpt2', 'mpt3'] as StepId[]).slice(0, Math.min(3, Math.max(MIN_TRIALS, trials)));
  return [...(needsCalibration ? (['glide'] as StepId[]) : []), 'noise', ...mpt, 'sentence'];
}

export function trialNo(step: StepId): number | null {
  return step === 'mpt1' ? 1 : step === 'mpt2' ? 2 : step === 'mpt3' ? 3 : null;
}
