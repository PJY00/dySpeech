// 노래방 따라 읽기 판정과 라운드 기록(api.md 7.2). 숫자는 모두 초기값이다.
// - 말속도 = N(정답 음절 수) ÷ T_speech(휴지를 뺀 발화 시간, 초). 서버 조음속도와 같은 정의, 검출 음절 수는 쓰지 않음
// - 변동계수 = σ(음절 간격의 표준편차) ÷ μ(음절 간격의 평균)
// - 통과 = 말속도가 목표 구간 안 + 변동계수가 기준 이하. 변동계수를 구하지 못하면 말속도로만 판정(검출기가 못 찾은 것을 사용자 실패로 세지 않음)
// - 라운드 성공률 = 통과 ÷ 읽은 문장 × 100. 80% 이상 up, 40% 이하 down, 그 사이 hold

import type { Decision, GameRun } from '../../session/types';

export const GAME = 'guide_reading';
export const MAX_LEVEL = 5;
/** 측정 블록 기준값도 저장된 기준도 없을 때의 기준 말속도(음절/초) */
export const DEFAULT_REFERENCE_RATE = 4.0;

/** 단계별 목표 구간 폭(기준 말속도 ± %)과 변동계수 기준. 단계가 오를수록 좁고 고르게 */
export const LEVEL_SPECS: readonly { level: number; tolerancePct: number; cvMax: number }[] = [
  { level: 1, tolerancePct: 30, cvMax: 0.6 },
  { level: 2, tolerancePct: 25, cvMax: 0.55 },
  { level: 3, tolerancePct: 20, cvMax: 0.5 },
  { level: 4, tolerancePct: 15, cvMax: 0.45 },
  { level: 5, tolerancePct: 12, cvMax: 0.4 },
];

export const UP_AT_PCT = 80;
export const DOWN_AT_PCT = 40;

export function levelSpec(level: number) {
  return LEVEL_SPECS[Math.min(MAX_LEVEL, Math.max(1, Math.round(level))) - 1];
}

export interface Target {
  /** 안내 커서 속도(음절/초) = 기준 말속도 */
  guideRate: number;
  min: number;
  max: number;
  cvMax: number;
}

export function targetFor(referenceRate: number, level: number): Target {
  const spec = levelSpec(level);
  const k = spec.tolerancePct / 100;
  return { guideRate: referenceRate, min: referenceRate * (1 - k), max: referenceRate * (1 + k), cvMax: spec.cvMax };
}

export interface SentenceMeasure {
  /** 말속도(음절/초). 발화를 찾지 못하면 null */
  rate: number | null;
  /** 휴지를 뺀 발화 시간(초) */
  speechS: number;
  cv: number | null;
  detected: number;
}

export interface SentenceResult {
  sentence_id: string;
  syllables: number;
  articulation_rate: number | null;
  cv: number | null;
  detected_syllables: number;
  speech_s: number;
  passed: boolean;
  /** 변동계수로 박자를 확인했는지. 음절 핵이 모자라 변동계수를 구하지 못하면 false(말속도로만 판정) */
  rhythm_checked: boolean;
  /** 통과하지 못한 이유(통과면 null): fast, slow, uneven, no_speech */
  reason: 'fast' | 'slow' | 'uneven' | 'no_speech' | null;
}

const r3 = (x: number) => Math.round(x * 1000) / 1000;

export function judgeSentence(id: string, syllables: number, m: SentenceMeasure, target: Target): SentenceResult {
  let reason: SentenceResult['reason'] = null;
  if (m.rate === null) reason = 'no_speech';
  else if (m.rate > target.max) reason = 'fast';
  else if (m.rate < target.min) reason = 'slow';
  else if (m.cv !== null && m.cv > target.cvMax) reason = 'uneven';
  return {
    sentence_id: id,
    syllables,
    articulation_rate: m.rate === null ? null : r3(m.rate),
    cv: m.cv === null ? null : r3(m.cv),
    detected_syllables: m.detected,
    speech_s: r3(m.speechS),
    passed: reason === null,
    rhythm_checked: m.cv !== null,
    reason,
  };
}

export const REASON_TEXT: Record<NonNullable<SentenceResult['reason']>, string> = {
  fast: '조금 빨랐어요. 안내 막대에 맞춰 천천히 읽어 보세요.',
  slow: '조금 느렸어요. 안내 막대를 따라가 보세요.',
  uneven: '빠르기가 고르지 않았어요. 한 글자씩 같은 박자로 읽어 보세요.',
  no_speech: '목소리가 잘 들리지 않았어요.',
};

export function roundDecision(results: SentenceResult[]): { successRatePct: number; decision: Decision; reason: string } {
  if (results.length === 0) return { successRatePct: 0, decision: 'deferred', reason: '읽은 문장이 없어 판정 보류' };
  const passed = results.filter((r) => r.passed).length;
  const pct = (passed / results.length) * 100;
  if (pct >= UP_AT_PCT) return { successRatePct: pct, decision: 'up', reason: `통과 ${passed}/${results.length}, 성공률 80% 이상` };
  if (pct <= DOWN_AT_PCT) return { successRatePct: pct, decision: 'down', reason: `통과 ${passed}/${results.length}, 성공률 40% 이하` };
  return { successRatePct: pct, decision: 'hold', reason: `통과 ${passed}/${results.length}, 성공률 40% 초과 80% 미만` };
}

export interface RoundContext {
  gameRunId: string;
  level: number;
  roundIndex: number;
  startedAt: string;
  referenceRate: number;
  referenceSource: string;
  target: Target;
}

/** 라운드 기록(api.md 7.2). params: 문장 ID·목표 구간·안내 속도, result: 문장별 말속도·변동계수·검출 음절 수·통과 여부 */
export function buildGameRun(ctx: RoundContext, results: SentenceResult[]): GameRun {
  const d = roundDecision(results);
  return {
    game_run_id: ctx.gameRunId,
    game: GAME,
    level: ctx.level,
    round_index: ctx.roundIndex,
    started_at: ctx.startedAt,
    params: {
      sentence_ids: results.map((r) => r.sentence_id),
      target_rate_range: [r3(ctx.target.min), r3(ctx.target.max)],
      guide_rate: r3(ctx.target.guideRate),
      cv_max: ctx.target.cvMax,
      reference_rate: r3(ctx.referenceRate),
      reference_source: ctx.referenceSource,
    },
    success_rate_pct: Math.round(d.successRatePct * 10) / 10,
    decision: d.decision,
    decision_reason: d.reason,
    result: { sentences: results },
  };
}
