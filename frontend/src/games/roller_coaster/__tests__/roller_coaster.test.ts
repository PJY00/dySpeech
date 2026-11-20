import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isUuidV4 } from '../../../session/uuid';
import { decimate, PitchTracker } from '../../spaceship/audio/PitchTracker';
import { stToHz } from '../../spaceship/audio/pitchMath';
import type { SpaceshipBaseline } from '../../spaceship/baseline';
import { buildRound, phraseAt, shapeAt, type Round } from '../../spaceship/contours';
import { addPitchFrame, advance, createGame } from '../../spaceship/engine';
import { computeGameRun } from '../../spaceship/metrics';
import { toRollerCoasterRun } from '../run';
import { buildGauge, buildScene, lookAheadTau, makeProjector, pitchAt, slopeAt, speedAt, trackSt, trendAhead } from '../view3d';

const SR = 48000;
const BASE: SpaceshipBaseline = { f0MedianHz: 140, noiseFloorDbfs: -60 };

function synth(dur: number, f0: (t: number) => number): Float32Array {
  const n = Math.round(dur * SR);
  const out = new Float32Array(n);
  let x = 5;
  let ph = 0;
  for (let i = 0; i < n; i++) {
    const f = f0(i / SR);
    let v = 0;
    if (f > 0) {
      ph += (2 * Math.PI * f) / SR;
      for (let k = 1; k <= 8; k++) v += Math.sin(k * ph) / k;
      v *= 0.05;
    }
    x = (x * 16807) % 2147483647;
    out[i] = v + (x / 2147483647 - 0.5) * 0.004;
  }
  return out;
}

type Voice = (round: Round, t: number) => number | null;

function play(level: number, voice: Voice) {
  const round = buildRound(level, undefined, 21);
  const game = createGame(round, BASE);
  const sig = synth(round.duration, (t) => {
    const st = voice(round, t);
    return st === null ? 0 : stToHz(st, BASE.f0MedianHz);
  });
  const tracker = new PitchTracker(SR / 6, { hopSec: 0.02 });
  for (let i = 0; i < sig.length; i += 960) {
    const now = (i + 960) / SR;
    const chunk = sig.subarray(i, i + 960);
    let e = 0;
    for (const v of chunk) e += v * v;
    const levelDbfs = 20 * Math.log10(Math.sqrt(e / chunk.length) + 1e-10);
    for (const f of tracker.push(decimate(chunk, 6))) addPitchFrame(game, round, BASE, { ...f, levelDbfs }, now);
    advance(game, round, now);
  }
  advance(game, round, round.duration + 0.01);
  return toRollerCoasterRun(
    computeGameRun(game, round, BASE, {
      roundIndex: 2,
      startedAt: new Date().toISOString(),
      sampleRate: SR,
      windowSec: 2048 / 48000,
      hopSec: 0.02,
      clarityThreshold: 0.85,
      platform: 'test',
      f0RangeHz: [75, 500],
      reactionAllowanceSec: 0.2,
    }),
  );
}

const follower: Voice = (round, t) => {
  const p = phraseAt(round, t);
  if (!p) return null;
  return p.warmup ? 0 : shapeAt(round, t - 0.15) ?? 0;
};
const monotone: Voice = (round, t) => (phraseAt(round, t) ? 0 : null);

test('목표 높이에 맞춰 내면 가까운 레일이 차 앞에 온다', () => {
  const round = buildRound(3, undefined, 5);
  const t = round.phrases[2].start + 1;
  const on = buildScene(round, t, trackSt(round, t), 400, 800, []);
  assert.ok(Math.abs(on.railNearY - on.noseY) < 0.5);
  const low = buildScene(round, t, trackSt(round, t) - 3, 400, 800, []);
  assert.ok(low.railNearY < low.noseY);
  const high = buildScene(round, t, trackSt(round, t) + 3, 400, 800, []);
  assert.ok(high.railNearY > high.noseY);
});

test('멀어질수록 작아지고 지평선 쪽으로 모인다', () => {
  const round = buildRound(1, undefined, 5);
  const pr = makeProjector(round, 0.5, 0, 400, 800);
  const near = pr.point(0.6, 0) as { y: number; s: number };
  const far = pr.point(lookAheadTau(round, 0.5), 0) as { y: number; s: number };
  assert.ok(far.s < near.s);
  assert.ok(far.y < near.y && far.y > pr.horizonY);
  assert.equal(pr.point(0.4, 0), null);
});

test('장면 경로가 비어 있지 않고 먹은 코인은 그리지 않는다', () => {
  const round = buildRound(2, undefined, 9);
  const t = round.stars[0] - 1;
  const end = lookAheadTau(round, t);
  const ahead = round.stars.map((x, i) => (x >= t && x <= end ? i : -1)).filter((i) => i >= 0);
  const all = buildScene(round, t, 0, 400, 800, []);
  assert.equal(all.coins.length, ahead.length);
  const got = round.stars.map((_, i) => i === ahead[0]);
  const fewer = buildScene(round, t, 0, 400, 800, got);
  assert.equal(fewer.coins.length, ahead.length - 1);
  for (const d of [all.bed, all.rails, all.ties + all.tiesActive + all.tiesUp + all.tiesDown, all.pillars]) {
    assert.ok(d.length > 0);
    assert.ok(!/NaN|undefined|Infinity/.test(d));
  }
});

test('오르막·내리막을 구분하고 미리 알려준다', () => {
  const round = buildRound(1, undefined, 3);
  const rise = round.phrases.find((p) => p.pattern === 'rise');
  const fall = round.phrases.find((p) => p.pattern === 'fall');
  assert.ok(rise && fall);
  const mid = (p: { start: number; end: number }) => (p.start + p.end) / 2;
  assert.equal(slopeAt(round, mid(rise)), 'up');
  assert.equal(slopeAt(round, mid(fall)), 'down');
  const bend = (p: { start: number; end: number }) => p.start + 0.35 * (p.end - p.start);
  assert.equal(trendAhead(round, bend(rise), 1.2, 1), 'up');
  assert.equal(trendAhead(round, bend(fall), 1.2, 1), 'down');
  assert.equal(trendAhead(round, rise.start, 0.5, 1), 'flat');
});

test('오르막에서는 고개가 들리고 느려지며, 내리막에서는 숙여지고 빨라진다', () => {
  const round = buildRound(1, undefined, 3);
  const rise = round.phrases.find((p) => p.pattern === 'rise');
  const fall = round.phrases.find((p) => p.pattern === 'fall');
  assert.ok(rise && fall);
  const steep = (p: { start: number; end: number }) => p.start + 0.6 * (p.end - p.start);
  assert.ok(pitchAt(round, steep(rise)) > 0.1);
  assert.ok(pitchAt(round, steep(fall)) < -0.1);
  assert.ok(speedAt(round, steep(rise)) < 0.9);
  assert.ok(speedAt(round, steep(fall)) > 1.1);
  const up = buildScene(round, steep(rise), trackSt(round, steep(rise)), 400, 800, []);
  const down = buildScene(round, steep(fall), trackSt(round, steep(fall)), 400, 800, []);
  assert.ok(up.horizonY > down.horizonY);
  assert.ok(Math.abs(up.railNearY - up.noseY) < 0.5);
});

test('작은 안내 그래프는 내 목소리 점과 목표선을 그린다', () => {
  const round = buildRound(2, undefined, 4);
  const t = round.phrases[1].start + 0.5;
  const hi = buildGauge(round, t, 3, 90, 150);
  const lo = buildGauge(round, t, -3, 90, 150);
  assert.ok(hi.dotY < lo.dotY);
  assert.ok(hi.target.startsWith('M') && hi.band.includes('Z'));
  assert.ok(!/NaN|undefined/.test(hi.target + hi.band));
});

test('곡선을 따라가면 통과하고 기록 이름은 roller_coaster다', () => {
  const run = play(1, follower);
  assert.equal(run.game, 'roller_coaster');
  assert.equal(run.result.passed, true);
  assert.ok(isUuidV4(run.game_run_id));
  assert.equal(run.round_index, 2);
});

test('한 높이로만 내면 통과하지 못한다', () => {
  const run = play(3, monotone);
  assert.equal(run.result.passed, false);
});
