import assert from 'node:assert/strict';
import { test } from 'node:test';

import { measurementBlock, noise, RATE, samplesOf } from '../../session/testing/synth';
import { encodeWav } from '../../session/wav';
import { AutoStop, checkRecording } from '../checks';
import { classifyInputs } from '../inputs';
import { blockSteps, trialNo } from '../steps';

const block = measurementBlock(5);
const wav = (x: Float32Array) => encodeWav(x, RATE);

test('단계: 첫 회차만 글라이드가 앞에 붙고, "아"는 2~3회', () => {
  assert.deepEqual(blockSteps(true), ['glide', 'noise', 'mpt1', 'mpt2', 'mpt3', 'sentence']);
  assert.deepEqual(blockSteps(false, 2), ['noise', 'mpt1', 'mpt2', 'sentence']);
  assert.deepEqual(blockSteps(false, 1), ['noise', 'mpt1', 'mpt2', 'sentence']);
  assert.equal(trialNo('mpt3'), 3);
  assert.equal(trialNo('sentence'), null);
});

test('녹음 검사: 정상 녹음은 받아들이고 분석 결과를 돌려준다', () => {
  const ctx = { noiseFloorDbfs: -60, calibration: null };
  const g = checkRecording('glide', wav(block.glide), { noiseFloorDbfs: null, calibration: null });
  assert.ok(g.ok, g.message);
  assert.ok(g.analysis?.calibration);
  const n = checkRecording('noise', wav(block.noiseFloor), ctx);
  assert.ok(n.ok);
  assert.equal(n.message, '좋아요.');
  const m = checkRecording('mpt1', wav(block.trials[0]), ctx);
  assert.ok(m.ok);
  assert.match(m.message, /6\.0초/);
  const s = checkRecording('sentence', wav(block.sentence), { noiseFloorDbfs: -60, calibration: g.analysis?.calibration ?? null });
  assert.ok(s.ok);
  assert.ok((s.analysis?.f0 ?? []).some((v) => v !== null));
});

test('녹음 검사: 소리가 없으면 다시 하기, 규격이 다르면 이대로 쓰기도 막는다', () => {
  const quiet = wav(noise(samplesOf(3), -60, 9));
  const ctx = { noiseFloorDbfs: -60, calibration: null };
  for (const step of ['glide', 'mpt2', 'sentence'] as const) {
    const c = checkRecording(step, quiet, ctx);
    assert.equal(c.ok, false, step);
    assert.equal(c.fatal, false, step);
  }
  const loud = checkRecording('noise', wav(noise(samplesOf(2.5), -30, 9)), ctx);
  assert.ok(loud.ok);
  assert.match(loud.message, /시끄러워요/);
  const stereo = wav(block.noiseFloor);
  new DataView(stereo.buffer).setUint16(22, 2, true);
  const bad = checkRecording('noise', stereo, ctx);
  assert.equal(bad.ok, false);
  assert.equal(bad.fatal, true);
  assert.match(bad.message, /모노/);
  assert.equal(checkRecording('noise', new Uint8Array(5), ctx).fatal, true);
});

test('자동 멈춤: 소리가 난 뒤 정해진 시간 조용하면 멈추고, 최대 시간에서 멈춘다', () => {
  const stop = new AutoStop({ floorDbfs: -60, silenceStopS: 1.5, minVoiceS: 0.3, maxS: 60 });
  let t = 0;
  const feed = (level: number, seconds: number) => {
    for (let i = 0; i < seconds / 0.02; i++) {
      t += 0.02;
      if (stop.push(level, 0.02)) return true;
    }
    return false;
  };
  assert.equal(feed(-60, 3), false, '소리가 나기 전 조용함은 멈추지 않음');
  assert.equal(feed(-20, 5), false);
  assert.equal(feed(-60, 1.4), false);
  assert.equal(feed(-60, 0.2), true);
  assert.ok(Math.abs(t - 9.5) < 0.05, String(t));
  const max = new AutoStop({ floorDbfs: null, maxS: 2.5 });
  let n = 0;
  while (!max.push(-20, 0.02)) n += 1;
  assert.equal(n, 124);
});

test('입력 장치: 3.5 mm·USB-C 헤드셋은 유선, 그 밖은 경고 대상', () => {
  assert.deepEqual(classifyInputs(['Built-in Mic', 'Wired Headset']), { inputDevice: 'wired_headset', wiredHeadset: true });
  assert.deepEqual(classifyInputs(['Built-in Mic', 'Other (22)']), { inputDevice: 'usb_headset', wiredHeadset: true });
  assert.deepEqual(classifyInputs(['Built-in Mic', 'Bluetooth SCO']), { inputDevice: 'bluetooth', wiredHeadset: false });
  assert.deepEqual(classifyInputs(['Built-in Mic']), { inputDevice: 'built_in_mic', wiredHeadset: false });
  assert.deepEqual(classifyInputs([]), { inputDevice: 'unknown', wiredHeadset: false });
});
