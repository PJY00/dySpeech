import assert from 'node:assert/strict';
import { test } from 'node:test';

import { noise, RATE, samplesOf } from '../../session/testing/synth';
import { frameLevels, median } from '../analysis';
import { bufferLevelDbfs, voiceBand, VoiceBandFilter } from '../filters';

const sine = (hz: number, seconds = 1, amp = 0.5) =>
  Float32Array.from({ length: samplesOf(seconds) }, (_, i) => amp * Math.sin((2 * Math.PI * hz * i) / RATE));
const levelOf = (x: Float32Array) => median(frameLevels(x, RATE));
const near = (actual: number, expected: number, tol: number, what: string) =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what}: ${actual.toFixed(3)} (정답 ${expected.toFixed(3)} ± ${tol})`);

test('통과 대역(1 kHz)은 그대로, 대역 밖(25 Hz, 12 kHz, 16 kHz)은 깎는다', () => {
  near(levelOf(voiceBand(sine(1000), RATE)) - levelOf(sine(1000)), 0, 0.05, '1 kHz 변화(dB)');
  assert.ok(levelOf(voiceBand(sine(12000), RATE)) - levelOf(sine(12000)) < -25, '12 kHz');
  assert.ok(levelOf(voiceBand(sine(16000), RATE)) - levelOf(sine(16000)) < -45, '16 kHz');
  assert.ok(levelOf(voiceBand(sine(25, 2), RATE)) - levelOf(sine(25, 2)) < -45, '25 Hz');
});

test('백색 잡음은 60 Hz~8 kHz 몫만 남는다: 10 × log₁₀(7940 ÷ 24000) ≈ −4.8 dB', () => {
  const x = noise(samplesOf(3), -60, 4);
  near(levelOf(voiceBand(x, RATE)), -60 + 10 * Math.log10(7940 / 24000), 0.5, '잡음 바닥(dBFS)');
});

test('위상 변화 없음: 앞뒤로 걸어 1 kHz 정현파의 위치가 그대로다', () => {
  const x = sine(1000, 0.2);
  const y = voiceBand(x, RATE);
  let dot = 0;
  let xx = 0;
  for (let i = 2000; i < 8000; i++) {
    dot += x[i] * y[i];
    xx += x[i] * x[i];
  }
  near(dot / xx, 1, 0.01, '같은 위상 상관');
});

test('실시간 필터: 버퍼를 나눠 넣어도 한 번에 넣은 것과 같고, 음량은 앞뒤 필터와 비슷하다', () => {
  const x = noise(samplesOf(2), -40, 8);
  const whole = new VoiceBandFilter(RATE).process(x);
  const split = new VoiceBandFilter(RATE);
  const parts: number[] = [];
  for (let i = 0; i < x.length; i += 960) parts.push(...split.process(x.subarray(i, i + 960)));
  for (let i = 0; i < x.length; i += 997) assert.ok(Math.abs(parts[i] - whole[i]) < 1e-6);
  const live = bufferLevelDbfs(whole.subarray(samplesOf(0.2)));
  near(live, levelOf(voiceBand(x, RATE)), 0.6, '실시간 음량과 앞뒤 필터 음량 차이(dB)');
});
