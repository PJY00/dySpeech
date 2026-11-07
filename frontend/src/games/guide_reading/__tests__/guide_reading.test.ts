import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { frameLevels, median } from '../../../measurement/analysis';
import { AutoStop } from '../../../measurement/checks';
import { bufferLevelDbfs, voiceBand, VoiceBandFilter } from '../../../measurement/filters';
import { MemoryStore } from '../../../session/store';
import { noise, RATE, samplesOf, speech } from '../../../session/testing/synth';
import type { SpeechSpec } from '../../../session/testing/synth';
import type { History } from '../../../session/types';
import { isUuidV4, uuidv4 } from '../../../session/uuid';
import { decodePcm16, parseWav } from '../../../session/wav';
import { analyzeSentence } from '../analysis';
import { END_PROGRESS_RATIO, fillsProgress, LATE_END_SILENCE_S, SentenceEnd } from '../endRule';
import { LiveReader } from '../live';
import { detectNuclei, intervalCv } from '../nuclei';
import { DEFAULT_DETECTION_RATIO, loadReadingState, recordDetection, shouldRebaseline, STATE_DOC } from '../reference';
import { buildGameRun, DEFAULT_REFERENCE_RATE, judgeSentence, LEVEL_SPECS, MIN_JUDGED_SENTENCES, roundDecision, targetFor } from '../scoring';
import type { SentenceResult } from '../scoring';
import { hangulSyllables, roundSentences, TRAINING_SENTENCES } from '../sentences';

const near = (actual: number, expected: number, tol: number, what: string) =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what}: ${actual.toFixed(3)} (정답 ${expected.toFixed(3)} ± ${tol.toFixed(3)})`);

/** 합성 낭독. 정답 말속도 = N ÷ (마지막 모음 끝 − 첫 자음 시작 − 쉼) */
function reading(syllables: number, vowelS: number[], seed = 3, pause: { after: number; s: number } | null = null, tailS = 1.2) {
  const spec: SpeechSpec = {
    syllables,
    pauseAfter: pause ? [pause.after] : [],
    pauseS: pause?.s ?? 0,
    vowelS,
    burstS: 0.025,
    f0StartHz: 150,
    f0EndHz: 120,
    levelDbfs: -22,
    leadS: 1,
    tailS,
    noiseDbfs: -60,
    seed,
  };
  const s = speech(spec);
  const truth = syllables / (s.syllables[s.syllables.length - 1].end - s.syllables[0].start - (pause?.s ?? 0));
  return { ...s, truth };
}

// 실제 녹음 폴더(eval_sentence_s1.wav, noise_floor.wav). 저장소에 없으므로 환경 변수로 받고, 없으면 건너뛴다
const REAL = process.env.DYSPEECH_REAL_DIR ?? '';
const hasReal = REAL !== '' && existsSync(join(REAL, 'eval_sentence_s1.wav')) && existsSync(join(REAL, 'noise_floor.wav'));
const loadReal = (name: string) => {
  const b = new Uint8Array(readFileSync(join(REAL, name)));
  return decodePcm16(b, parseWav(b));
};

test('훈련 문장: 5~10개, s1 제외, 숫자·외래어 없음, 적어 둔 음절 수가 한글 음절 글자 수와 같다', () => {
  assert.ok(TRAINING_SENTENCES.length >= 5 && TRAINING_SENTENCES.length <= 10);
  const s1Words = ['우리', '저녁', '노래', '부르며', '마을', '걸었습니다'];
  for (const s of TRAINING_SENTENCES) {
    assert.equal(s.syllables, hangulSyllables(s.text), s.id);
    assert.doesNotMatch(s.text, /[0-9A-Za-z]/, s.id);
    for (const w of s1Words) assert.ok(!s.text.includes(w), `${s.id}에 s1 낱말 ${w}`);
  }
  assert.equal(new Set(TRAINING_SENTENCES.map((s) => s.id)).size, TRAINING_SENTENCES.length);
  assert.deepEqual(roundSentences(1).map((s) => s.id), ['g01', 'g02', 'g03', 'g04', 'g05']);
  assert.deepEqual(roundSentences(2).map((s) => s.id), ['g06', 'g07', 'g08', 'g09', 'g10']);
  assert.deepEqual(roundSentences(3).map((s) => s.id), ['g01', 'g02', 'g03', 'g04', 'g05']);
});

test('음절 핵과 변동계수: 고른 낭독은 정답 개수를 찾고 변동계수가 작으며, 휴지를 건너는 간격은 뺀다', () => {
  const even = reading(12, [0.16]);
  const levels = frameLevels(voiceBand(even.samples, RATE), RATE);
  const peaks = detectNuclei(levels, Array.from(levels, () => true), -60);
  assert.equal(peaks.length, 12);
  assert.ok((intervalCv(peaks) ?? 1) < 0.1, `변동계수 ${intervalCv(peaks)}`);
  // 간격 10, 10, 50(휴지), 10 프레임 → 휴지를 빼면 변동계수 0
  const mask = Array.from({ length: 100 }, (_, f) => !(f > 25 && f < 65));
  near(intervalCv([0, 10, 20, 70, 80], mask) ?? 1, 0, 1e-9, '휴지 뺀 변동계수');
  assert.ok((intervalCv([0, 10, 20, 70, 80]) ?? 0) > 0.5);
  assert.equal(intervalCv([0, 10]), null);
});

test('문장 분석: 말속도(N ÷ 휴지를 뺀 발화 시간)가 정답과 맞고, 고르지 않은 낭독은 변동계수가 크다', () => {
  for (const vowelS of [[0.16], [0.25]]) {
    const r = reading(13, vowelS, 9);
    const a = analyzeSentence(r.samples, RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
    assert.ok(a.rate);
    near(a.rate, r.truth, r.truth * 0.06, `말속도(모음 ${vowelS[0]}초)`);
    assert.ok(a.detected >= 11 && a.detected <= 13, `검출 ${a.detected}`);
  }
  const uneven = reading(13, [0.08, 0.3, 0.1, 0.35], 4);
  const u = analyzeSentence(uneven.samples, RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
  const even = reading(13, [0.16], 4);
  const e = analyzeSentence(even.samples, RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
  assert.ok((u.cv ?? 0) > (e.cv ?? 1) + 0.2, `고르지 않음 ${u.cv}, 고름 ${e.cv}`);
  const silent = analyzeSentence(noise(samplesOf(3), -60, 2), RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
  assert.equal(silent.rate, null);
});

test('실제 녹음(s1, 21음절): 말소리 대역 음절 핵 검출과 말속도(데이터가 있을 때만)', { skip: hasReal ? false : 'DYSPEECH_REAL_DIR 없음' }, () => {
  const x = loadReal('eval_sentence_s1.wav');
  const nf = median(frameLevels(voiceBand(loadReal('noise_floor.wav'), RATE), RATE));
  const a = analyzeSentence(x, RATE, 21, { noiseFloorDbfs: nf, searchRange: { min: 75, max: 500 } });
  console.log(`[real] 검출 ${a.detected}/21, 말속도 ${a.rate?.toFixed(2)}음절/초(서버 조음속도 7.37), 발화 ${a.speechS.toFixed(2)}초, 변동계수 ${a.cv?.toFixed(2)}`);
  assert.ok(a.detected >= 12, `검출 ${a.detected}`);
  assert.ok(a.rate && Math.abs(a.rate - 7.37) / 7.37 < 0.15, `말속도 ${a.rate}`);
  assert.ok(a.cv !== null);
});

function liveRun(samples: Float32Array, syllables: number, rate: number, nf: number | null) {
  const live = new LiveReader({ sampleRate: RATE, syllables, referenceRate: rate, detectionRatio: DEFAULT_DETECTION_RATIO, noiseFloorDbfs: nf });
  const floor = nf ?? -60;
  const trace: { t: number; est: number; speaking: boolean }[] = [];
  for (let i = 0; i < samples.length; i += 960) {
    const chunk = samples.subarray(i, i + 960);
    live.push(chunk);
    let s = 0;
    for (let k = 0; k < chunk.length; k++) s += chunk[k] * chunk[k];
    const lvl = 20 * Math.log10(Math.sqrt(s / Math.max(1, chunk.length)) + 1e-10);
    trace.push({ t: (i + chunk.length) / RATE, est: live.estimate, speaking: lvl > floor + 10 });
  }
  // 말하는 동안 추정이 그대로인 가장 긴 시간(초). 상한(N − 0.5)에 닿은 뒤는 세지 않는다
  let stall = 0;
  let run = 0;
  for (let k = 1; k < trace.length; k++) {
    if (trace[k].speaking && trace[k].est === trace[k - 1].est && trace[k].est < syllables - 0.5) run += 0.02;
    else run = 0;
    stall = Math.max(stall, run);
  }
  return { live, trace, stall };
}

test('실시간 진행 표시: 줄지 않고, 말하는 동안 멈추지 않으며, 끝내기 전에는 N을 넘지 않는다', () => {
  const r = reading(13, [0.16], 6);
  const { live, trace, stall } = liveRun(r.samples, 13, 4.5, -60);
  for (let k = 1; k < trace.length; k++) assert.ok(trace[k].est >= trace[k - 1].est);
  assert.ok(stall <= 0.3, `멈춤 ${stall.toFixed(2)}초`);
  assert.ok(live.estimate >= 0.8 * 13 && live.estimate <= 12.5, `끝 추정 ${live.estimate}`);
  live.finish();
  assert.equal(live.estimate, 13);
});

test('실시간 진행 표시: 실제 녹음에서도 말하는 동안 멈추지 않는다(데이터가 있을 때만)', { skip: hasReal ? false : 'DYSPEECH_REAL_DIR 없음' }, () => {
  const nf = median(frameLevels(voiceBand(loadReal('noise_floor.wav'), RATE), RATE));
  const { live, stall } = liveRun(loadReal('eval_sentence_s1.wav'), 21, 6, nf);
  console.log(`[real live] 검출 ${live.detected}, 끝 추정 ${live.estimate.toFixed(1)}/21, 가장 긴 멈춤 ${stall.toFixed(2)}초`);
  assert.ok(stall <= 0.4, `멈춤 ${stall}`);
  assert.ok(live.estimate >= 0.6 * 21, `끝 추정 ${live.estimate}`);
});

test('판정: 목표 구간(기준 ± 단계별 폭)과 변동계수 기준, 라운드 성공률로 up·hold·down', () => {
  const t = targetFor(4, 1);
  near(t.min, 2.8, 1e-9, '1단계 아래');
  near(t.max, 5.2, 1e-9, '1단계 위');
  assert.equal(t.guideRate, 4);
  assert.ok(targetFor(4, 5).max - targetFor(4, 5).min < t.max - t.min);
  assert.deepEqual(LEVEL_SPECS.map((l) => l.level), [1, 2, 3, 4, 5]);
  const m = (rate: number | null, cv: number | null) => ({ rate, speechS: 3, cv, detected: 9 });
  assert.equal(judgeSentence('g01', 11, m(4, 0.3), t).passed, true);
  assert.equal(judgeSentence('g01', 11, m(5.5, 0.3), t).reason, 'fast');
  assert.equal(judgeSentence('g01', 11, m(2.5, 0.3), t).reason, 'slow');
  assert.equal(judgeSentence('g01', 11, m(4, 0.9), t).reason, 'uneven');
  assert.equal(judgeSentence('g01', 11, m(4, 0.3), t).rhythm_checked, true);
  // 변동계수를 구하지 못하면 말속도로만 판정: 구간 안이면 통과, 리듬은 확인하지 못했다고 남긴다
  const noRhythm = judgeSentence('g01', 11, m(4, null), t);
  assert.deepEqual([noRhythm.passed, noRhythm.reason, noRhythm.cv, noRhythm.rhythm_checked], [true, null, null, false]);
  assert.deepEqual([judgeSentence('g01', 11, m(9, null), t).reason, judgeSentence('g01', 11, m(9, null), t).rhythm_checked], ['fast', false]);
  assert.equal(judgeSentence('g01', 11, m(null, null), t).reason, 'no_speech');
  const res = (passed: number) =>
    Array.from({ length: 5 }, (_, i) => judgeSentence(`g0${i + 1}`, 11, m(i < passed ? 4 : 9, 0.3), t));
  assert.equal(roundDecision(res(4)).decision, 'up');
  assert.equal(roundDecision(res(4)).successRatePct, 80);
  assert.equal(roundDecision(res(3)).decision, 'hold');
  assert.equal(roundDecision(res(2)).decision, 'down');
  assert.equal(roundDecision(res(2)).successRatePct, 40);
  assert.equal(roundDecision([]).decision, 'deferred');
});

test('라운드 기록: api.md 7.2 형식, params(문장 ID·목표 구간·안내 속도), result(문장별 값)', () => {
  const t = targetFor(4.2, 2);
  const results: SentenceResult[] = roundSentences(1).map((s, i) => judgeSentence(s.id, s.syllables, { rate: 4 + i * 0.1, speechS: 2.5, cv: 0.2, detected: s.syllables - 2 }, t));
  const run = buildGameRun({ gameRunId: uuidv4(), level: 2, roundIndex: 1, startedAt: '2026-11-08T09:00:00.000Z', referenceRate: 4.2, referenceSource: 'baseline', target: t }, results);
  assert.ok(isUuidV4(run.game_run_id));
  assert.equal(run.game, 'guide_reading');
  assert.match(run.game, /^[a-z][a-z0-9_]*$/);
  assert.equal(run.success_rate_pct, 100);
  assert.equal(run.decision, 'up');
  const params = run.params as Record<string, unknown>;
  assert.deepEqual(params.sentence_ids, ['g01', 'g02', 'g03', 'g04', 'g05']);
  assert.deepEqual(params.target_rate_range, [3.15, 5.25], '2단계: 4.2 ± 25%');
  assert.equal(params.guide_rate, 4.2);
  const sentences = (run.result as { sentences: SentenceResult[] }).sentences;
  assert.deepEqual(Object.keys(sentences[0]).sort(), ['articulation_rate', 'cv', 'detected_syllables', 'end_reason', 'judged', 'passed', 'reason', 'rhythm_checked', 'sentence_id', 'speech_s', 'syllables']);
  assert.ok(JSON.stringify(run).length < 10_000);
});

const history = (status: History['metrics'][number]['status'], latest: number | null): History => ({
  user_id: 'u',
  metrics: [
    {
      metric: 'articulation_rate',
      unit: 'syllables/s',
      desired_direction: 'none',
      baseline: { session_id: 's0', value: 4 },
      series: [],
      latest: latest === null ? null : { value: latest, change: latest - 4 },
      mdc: null,
      status,
    },
  ],
  recent_device_measurements: [],
});

test('기준 말속도: 처음은 측정 블록 값(없으면 기본값). 진전도 increase·decrease는 다시 잡을지만 정하고, 값은 그날 기기 조음속도', async () => {
  const store = new MemoryStore();
  let s = await loadReadingState(store, 'u1', 4.6, null);
  assert.deepEqual([s.referenceRate, s.source], [4.6, 'baseline']);
  s = await loadReadingState(store, 'u1', 3.0, history('pending_mdc', 5.5));
  assert.deepEqual([s.referenceRate, s.source], [4.6, 'stored'], 'pending_mdc: 유지(그날 값으로 바꾸지 않음)');
  s = await loadReadingState(store, 'u1', 3.0, history('no_change', 5.5));
  assert.deepEqual([s.referenceRate, s.source], [4.6, 'stored'], 'no_change: 유지');
  s = await loadReadingState(store, 'u1', 5.0, history('increase', 9.9));
  assert.deepEqual([s.referenceRate, s.source], [5.0, 'rebaseline'], 'increase: 서버 latest(9.9)가 아니라 그날 기기 값(5.0)');
  s = await loadReadingState(store, 'u1', 4.2, history('decrease', 1.1));
  assert.deepEqual([s.referenceRate, s.source], [4.2, 'rebaseline']);
  s = await loadReadingState(store, 'u1', null, history('increase', 9.9));
  assert.deepEqual([s.referenceRate, s.source], [4.2, 'stored'], '그날 기기 값이 없으면 저장된 값 유지');
  const fresh = await loadReadingState(store, 'u2', null, history('increase', 6));
  assert.deepEqual([fresh.referenceRate, fresh.source], [DEFAULT_REFERENCE_RATE, 'default']);
  assert.equal(shouldRebaseline(history('low_reliability', 9)), false);
  assert.equal(shouldRebaseline(null), false);
  assert.ok(store.docs.has(STATE_DOC));
});

test('검출 비율: 문장마다 0.7 × 이전 + 0.3 × (검출 ÷ 정답), 0.3~1.2 안', async () => {
  const store = new MemoryStore();
  await loadReadingState(store, 'u', 4, null);
  near(await recordDetection(store, 'u', 10, 10), 0.7 * 0.7 + 0.3 * 1, 1e-9, '첫 갱신');
  for (let i = 0; i < 30; i++) await recordDetection(store, 'u', 0, 10);
  near(await recordDetection(store, 'u', 0, 10), 0.3, 1e-9, '아래 한계');
  const s = await loadReadingState(store, 'u', 4, null);
  near(s.detectionRatio, 0.3, 1e-9, '저장됨');
});

/** 녹음을 20 ms 버퍼로 흘리며 끝내기 규칙을 돌린다. 끝난 시각(초)과 이유 */
function runEnd(samples: Float32Array, syllables: number, guideRate: number, rule: 'new' | 'old' = 'new') {
  const live = new LiveReader({ sampleRate: RATE, syllables, referenceRate: guideRate, detectionRatio: DEFAULT_DETECTION_RATIO, noiseFloorDbfs: -60 });
  const filter = new VoiceBandFilter(RATE);
  const guideS = syllables / guideRate;
  const end = new SentenceEnd({ syllables, floorDbfs: -60, guideStartS: 1, guideS, maxS: 1 + guideS * 2.5 + 4 });
  const old = new AutoStop({ floorDbfs: -60, silenceStopS: 1, minVoiceS: 0.5, maxS: 1 + guideS * 2.5 + 4 });
  for (let i = 0; i < samples.length; i += 960) {
    const chunk = samples.subarray(i, i + 960);
    live.push(chunk);
    const level = bufferLevelDbfs(filter.process(chunk));
    const t = (i + chunk.length) / RATE;
    if (rule === 'new' ? end.push(level, chunk.length / RATE, live.estimate) : old.push(level, chunk.length / RATE)) {
      return { stopS: t, reason: rule === 'new' ? end.reason : 'old', estimate: live.estimate, guideEndS: 1 + guideS };
    }
  }
  return { stopS: samples.length / RATE, reason: null, estimate: live.estimate, guideEndS: 1 + guideS };
}

test('문장 끝내기: 문장 중간에 1.5초 쉬어도 끝까지 녹음되고 말속도가 맞게 나온다', () => {
  const r = reading(13, [0.16], 12, { after: 5, s: 1.5 }, 3);
  const lastEnd = r.syllables[r.syllables.length - 1].end;
  const old = runEnd(r.samples, 13, r.truth, 'old');
  assert.ok(old.stopS < lastEnd, `예전 규칙은 쉼에서 잘렸다: ${old.stopS.toFixed(2)}초 < ${lastEnd.toFixed(2)}초`);
  const e = runEnd(r.samples, 13, r.truth);
  assert.equal(e.reason, 'progress', `80% 넘게 읽은 뒤 1초 무음으로 끝: 추정 ${e.estimate.toFixed(1)}`);
  assert.ok(e.stopS > lastEnd && e.stopS < lastEnd + 1.4, `끝난 시각 ${e.stopS.toFixed(2)}초(마지막 음절 끝 ${lastEnd.toFixed(2)}초)`);
  const a = analyzeSentence(r.samples.subarray(0, Math.round(e.stopS * RATE)), RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
  assert.ok(a.rate);
  near(a.rate, r.truth, r.truth * 0.06, '말속도(쉼 뺌)');
  assert.ok(END_PROGRESS_RATIO === 0.8);
});

test('문장 끝내기: 80% 미만에서 멈추면 안내 커서가 끝난 뒤 3초 조용할 때 끝내고, 커서가 끝나기 전에는 끝내지 않는다', () => {
  // 13음절 중 5음절만 읽고 멈춤(뒤는 무음 10초)
  const r = reading(5, [0.16], 21, null, 10);
  const guideRate = 3; // 13음절 ÷ 3음절/초 = 커서 4.3초
  const e = runEnd(r.samples, 13, guideRate);
  assert.equal(e.reason, 'after_guide');
  near(e.stopS, e.guideEndS + LATE_END_SILENCE_S, 0.05, '끝난 시각 = 커서 끝 + 3초');
  assert.ok(e.estimate < 0.8 * 13);
  // 판정: N(13) ÷ 읽은 부분(5음절)의 발화 시간이면 약 2.6배로 잡혀 fast가 되던 것을, 판정하지 않는다
  const a = analyzeSentence(r.samples.subarray(0, Math.round(e.stopS * RATE)), RATE, 13, { noiseFloorDbfs: -60, searchRange: { min: 75, max: 500 } });
  const t = targetFor(guideRate, 1);
  assert.equal(judgeSentence('g01', 13, a, t).reason, 'fast', '끝낸 이유를 보지 않으면 fast로 실패');
  const j = judgeSentence('g01', 13, a, t, e.reason ?? 'max');
  assert.deepEqual([j.judged, j.passed, j.reason, j.end_reason, j.articulation_rate], [false, false, 'incomplete', 'after_guide', null]);
  assert.equal(fillsProgress(e.reason), false, '진행 표시는 끝까지 채우지 않는다');
});

test('문장 끝내기: 최대 시간이 되면 끝낸다', () => {
  const end = new SentenceEnd({ syllables: 10, floorDbfs: -60, guideStartS: 1, guideS: 2, maxS: 5 });
  let t = 0;
  while (!end.push(-20, 0.02, 0)) t += 0.02;
  assert.equal(end.reason, 'max');
  near(t + 0.02, 5, 0.03, '최대 시간');
});

test('끝까지 읽지 못한 문장: 말속도로 판정하지 않고 성공률 분모에서 뺀다. 판정 문장이 3개 미만이면 deferred', () => {
  const t = targetFor(4, 1);
  const m = (rate: number) => ({ rate, speechS: 3, cv: 0.3, detected: 9 });
  const ok = (id: string) => judgeSentence(id, 11, m(4), t);
  const bad = (id: string) => judgeSentence(id, 11, m(9), t);
  const cut = (id: string, why: 'after_guide' | 'max') => judgeSentence(id, 11, m(4), t, why);
  assert.equal(ok('g01').end_reason, 'progress');
  assert.equal(ok('g01').judged, true);
  for (const why of ['after_guide', 'max'] as const) {
    const c = cut('g02', why);
    assert.deepEqual([c.judged, c.passed, c.reason, c.end_reason, c.articulation_rate, c.rhythm_checked], [false, false, 'incomplete', why, null, false]);
  }
  // 판정 3개(모두 통과) + 끝까지 못 읽은 시도 2개 → 3/3 = 100% up
  const three = roundDecision([ok('g01'), cut('g02', 'after_guide'), ok('g02'), ok('g03'), cut('g04', 'max')]);
  assert.deepEqual([three.decision, three.successRatePct], ['up', 100]);
  assert.match(three.reason, /끝까지 읽지 못한 시도 2개 제외/);
  // 판정 3개 중 1개 통과 → 33% down
  assert.equal(roundDecision([ok('g01'), bad('g02'), bad('g03'), cut('g04', 'max')]).decision, 'down');
  // 판정 2개뿐 → deferred
  assert.equal(MIN_JUDGED_SENTENCES, 3);
  const two = roundDecision([ok('g01'), ok('g02'), cut('g03', 'after_guide'), cut('g04', 'after_guide'), cut('g05', 'max')]);
  assert.equal(two.decision, 'deferred');
  assert.match(two.reason, /3개 미만/);
});

test('다시 읽기: 결과에는 시도마다 남고, params.sentence_ids는 라운드의 문장 5개 그대로', () => {
  const t = targetFor(4, 1);
  const ids = roundSentences(1).map((x) => x.id);
  const results = [
    judgeSentence('g01', 11, { rate: 4, speechS: 2, cv: 0.2, detected: 9 }, t, 'after_guide'),
    ...ids.map((id) => judgeSentence(id, 11, { rate: 4, speechS: 2.7, cv: 0.2, detected: 9 }, t)),
  ];
  const run = buildGameRun({ gameRunId: uuidv4(), level: 1, roundIndex: 1, startedAt: '2026-11-08T09:00:00.000Z', referenceRate: 4, referenceSource: 'baseline', target: t, sentenceIds: ids }, results);
  assert.deepEqual((run.params as Record<string, unknown>).sentence_ids, ids);
  assert.equal((run.result as { sentences: unknown[] }).sentences.length, 6);
  assert.equal(run.success_rate_pct, 100);
  assert.deepEqual([fillsProgress('progress'), fillsProgress('max'), fillsProgress(null)], [true, false, false]);
});
