import assert from 'node:assert/strict';
import { test } from 'node:test';

import { harmonic, measurementBlock, noise, place, RATE, samplesOf } from '../../session/testing/synth';
import { calibrationRange, computeBlock, frameLevels, longestPhonation, median, sentencePitch, speechMask } from '../analysis';
import { cleanF0, pitchTrack, searchRange } from '../pitch';

const block = measurementBlock();
const near = (actual: number, expected: number, tol: number, what: string) =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what}: ${actual} (정답 ${expected} ± ${tol})`);

test('프레임 음량: 30 ms 길이·10 ms 간격, 잡음 바닥은 noise_floor 녹음의 중앙값', () => {
  const levels = frameLevels(block.noiseFloor, RATE);
  assert.equal(levels.length, Math.floor((block.noiseFloor.length - 1440) / 480) + 1);
  near(median(levels), -60, 0.3, '잡음 바닥');
  // 정현파 진폭 1의 제곱평균제곱근 진폭은 1/√2 → −3.01 dBFS
  const sine = Float32Array.from({ length: RATE }, (_, i) => Math.sin((2 * Math.PI * 1000 * i) / RATE));
  near(median(frameLevels(sine, RATE)), -3.01, 0.01, '정현파 음량');
});

test('최대발성지속시간과 "아" 음량: 0.2초 이하 끊김은 잇고, 이은 곳은 음량 중앙값에서 뺀다', () => {
  const floor = median(frameLevels(block.noiseFloor, RATE));
  const p = longestPhonation(frameLevels(block.trials[1], RATE), floor);
  assert.ok(p);
  near(p.durationS, 7.5, 0.05, '발성 길이');
  near(p.medianLevel, -20, 0.3, '"아" 음량');
  // 3초 발성 + 0.15초 끊김 + 2초 발성 → 하나로 5.15초
  const bg = noise(samplesOf(7), -60, 11);
  const broken = place(place(bg, harmonic(150, samplesOf(3), -20), samplesOf(0.5)), harmonic(150, samplesOf(2), -20), samplesOf(3.65));
  const q = longestPhonation(frameLevels(broken, RATE), -60);
  assert.ok(q);
  near(q.durationS, 5.15, 0.05, '이은 발성 길이');
});

test('기본주파수: 150 Hz 배음 소리를 150 Hz로 찾고, 탐색 범위 밖은 버린다', () => {
  const x = place(noise(samplesOf(2), -60, 5), harmonic(150, samplesOf(1.5), -20), samplesOf(0.25));
  const levels = frameLevels(x, RATE);
  const track = cleanF0(pitchTrack(x, RATE, levels.length, { min: 75, max: 500 }));
  const voiced = track.filter((v): v is number => v !== null);
  assert.ok(voiced.length > 120, `유성 프레임 ${voiced.length}`);
  near(median(voiced), 150, 0.5, '기본주파수');
  const narrow = pitchTrack(x, RATE, levels.length, { min: 200, max: 500 });
  assert.equal(narrow.filter((v) => v !== null).length, 0);
});

test('옥타브 오류 보정: 중앙값에서 10반음 이상 벗어난 값은 2배·1/2로 접는다', () => {
  const raw: (number | null)[] = [null, ...Array(20).fill(150), 300, 300, ...Array(20).fill(150), null];
  const fixed = cleanF0(raw).filter((v): v is number => v !== null);
  assert.ok(fixed.every((v) => v === 150), JSON.stringify(fixed));
});

test('캘리브레이션 글라이드: 5·95백분위가 지수 글라이드 정답과 맞다', () => {
  const levels = frameLevels(block.glide, RATE);
  const range = calibrationRange(pitchTrack(block.glide, RATE, levels.length, { min: 75, max: 500 }));
  assert.ok(range);
  const r = block.truth.glide.endHz / block.truth.glide.startHz;
  near(range.min, block.truth.glide.startHz * r ** 0.05, block.truth.glide.startHz * r ** 0.05 * 0.03, '최저');
  near(range.max, block.truth.glide.startHz * r ** 0.95, block.truth.glide.startHz * r ** 0.95 * 0.03, '최고');
});

test('탐색 범위: 서버 규칙(최저 × 0.8과 75 Hz 중 낮은 쪽, 최고 × 1.2, 좁으면 75~500)', () => {
  assert.deepEqual(searchRange(null), { min: 75, max: 500 });
  const r = searchRange({ min: 116, max: 295 });
  assert.equal(r.min, 75);
  near(r.max, 354, 1e-9, '위쪽');
  assert.deepEqual(searchRange({ min: 60, max: 700 }), { min: 50, max: 800 });
  assert.deepEqual(searchRange({ min: 200, max: 300 }), { min: 75, max: 360 });
  // 위쪽(최고 × 1.2 = 144 Hz)이 아래쪽(75 Hz)의 2배 이하 → 75~500 Hz
  assert.deepEqual(searchRange({ min: 100, max: 120 }), { min: 75, max: 500 });
});

test('평가 문장: 발화는 음량으로 나누고, 유성 기본주파수 중앙값과 유성 비율을 구한다', () => {
  const levels = frameLevels(block.sentence, RATE);
  const mask = speechMask(levels, -60);
  const first = block.truth.syllables[0].start;
  const last = block.truth.syllables[block.truth.syllables.length - 1].end;
  const speechS = mask.filter(Boolean).length * 0.01;
  near(speechS, last - first - 0.4, 0.15, '발화 시간(휴지 0.4초 뺌)');
  const sp = sentencePitch(levels, pitchTrack(block.sentence, RATE, levels.length, { min: 75, max: 500 }), -60);
  assert.ok(sp);
  const [a, b] = block.truth.sentenceF0Hz;
  near(sp.f0MedianHz, (a + b) / 2, ((a + b) / 2) * 0.04, '기본주파수 중앙값');
  assert.ok(sp.voicedRatio >= 0.5, `유성 비율 ${sp.voicedRatio}`);
});

test('측정 블록 전체: 기기 지표 4개(단위 포함)와 기준값', () => {
  const noiseLevels = frameLevels(block.noiseFloor, RATE);
  const glideLevels = frameLevels(block.glide, RATE);
  const calibration = calibrationRange(pitchTrack(block.glide, RATE, glideLevels.length, { min: 75, max: 500 }));
  const range = searchRange(calibration);
  const sentenceLevels = frameLevels(block.sentence, RATE);
  const result = computeBlock({
    noise: noiseLevels,
    trials: block.trials.map((t) => frameLevels(t, RATE)),
    sentence: { levels: sentenceLevels, f0: pitchTrack(block.sentence, RATE, sentenceLevels.length, range) },
    calibration,
    storedCalibration: null,
  });
  assert.deepEqual(result.measurements.map((m) => [m.metric, m.unit]), [
    ['noise_floor', 'dBFS'],
    ['max_phonation_time', 's'],
    ['sustained_level', 'dBFS'],
    ['f0_median', 'Hz'],
  ]);
  near(result.baselines.noiseFloorDbfs as number, -60, 0.3, '잡음 바닥');
  near(result.baselines.maxPhonationTimeS as number, 7.5, 0.05, '최대발성지속시간');
  near(result.baselines.sustainedLevelDbfs as number, -20, 0.3, '"아" 음량');
  assert.ok(result.baselines.f0MedianHz);
  assert.deepEqual(result.baselines.f0RangeHz, calibration);
  assert.deepEqual(result.baselines.f0SearchRangeHz, range);
  assert.deepEqual(result.notes, []);
});

test('noise_floor 녹음이 없으면 녹음마다 하위 10백분위를 잡음 바닥으로 쓰고 신뢰 낮음으로 표시한다', () => {
  const result = computeBlock({ noise: null, trials: [frameLevels(block.trials[0], RATE)], sentence: null, calibration: null, storedCalibration: null });
  assert.equal(result.baselines.noiseFloorDbfs, null);
  assert.ok(result.measurements.every((m) => m.low_reliability));
  near(result.baselines.maxPhonationTimeS as number, 6, 0.05, '최대발성지속시간');
  assert.equal(result.notes.length, 1);
});
