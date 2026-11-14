import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isUuidV4 } from '../../../session/uuid';
import { decimate, PitchTracker } from '../audio/PitchTracker';
import { hzToSt, median, stToHz } from '../audio/pitchMath';
import type { SpaceshipBaseline } from '../baseline';
import { buildRound, deviationFrom, LEVELS, phraseAt, shapeAt, type Round } from '../contours';
import { addPitchFrame, advance, createGame, type GameState } from '../engine';
import { computeGameRun, type SpaceshipGameRun } from '../metrics';

const SR = 48000;
const BASE: SpaceshipBaseline = { f0MedianHz: 120, noiseFloorDbfs: -60 };

function noise(seed: number): () => number {
  let x = seed;
  return () => {
    x = (x * 16807) % 2147483647;
    return x / 2147483647 - 0.5;
  };
}

function synth(dur: number, f0: (t: number) => number, amp = 0.1, noiseAmp = 0.002): Float32Array {
  const n = Math.round(dur * SR);
  const out = new Float32Array(n);
  const rnd = noise(7);
  let ph = 0;
  for (let i = 0; i < n; i++) {
    const f = f0(i / SR);
    let v = 0;
    if (f > 0) {
      ph += (2 * Math.PI * f) / SR;
      for (let k = 1; k <= 8; k++) v += Math.sin(k * ph) / k;
      v *= amp / 2;
    }
    out[i] = v + rnd() * 2 * noiseAmp;
  }
  return out;
}

type Voice = (round: Round, t: number) => number | null;

function play(level: number, voice: Voice, baseline = BASE, actualRefHz = BASE.f0MedianHz): { run: SpaceshipGameRun; game: GameState } {
  const round = buildRound(level, undefined, 11);
  const game = createGame(round, baseline);
  const sig = synth(round.duration, (t) => {
    const st = voice(round, t);
    return st === null ? 0 : stToHz(st, actualRefHz);
  });
  const tracker = new PitchTracker(SR);
  const chunk = 960;
  let ft = 0;
  for (let i = 0; i < sig.length; i += chunk) {
    const now = (i + chunk) / SR;
    for (const f of tracker.push(sig.subarray(i, i + chunk))) addPitchFrame(game, round, baseline, f, now);
    while (ft <= now) {
      advance(game, round, ft);
      ft += 1 / 60;
    }
  }
  advance(game, round, round.duration + 0.01);
  const run = computeGameRun(game, round, baseline, {
    roundIndex: 1,
    startedAt: new Date().toISOString(),
    sampleRate: SR,
    windowSec: 2048 / 48000,
    hopSec: 0.01,
    clarityThreshold: 0.85,
    platform: 'test',
    f0RangeHz: [75, 500],
    reactionAllowanceSec: 0.2,
  });
  return { run, game };
}

const follower: Voice = (round, t) => {
  const p = phraseAt(round, t);
  if (!p) return null;
  const v = shapeAt(round, t - 0.15);
  return p.warmup ? 0 : v ?? 0;
};
const monotone: Voice = (round, t) => (phraseAt(round, t) ? 0 : null);
const silent: Voice = () => null;

test('기본주파수를 0.2반음 안으로 따라간다', () => {
  const f = (t: number) => 100 * 1.6 ** (t / 2);
  const tracker = new PitchTracker(SR);
  const frames = tracker.push(synth(2, f));
  const errs = frames.filter((x) => x.hz !== null).map((x) => Math.abs(hzToSt(x.hz as number, f(x.time))));
  assert.ok(errs.length > 150);
  assert.ok(median(errs) < 0.2, `median ${median(errs)}`);
});

test('실시간용(8 kHz, 20 ms 간격)으로 재도 0.2반음 안으로 따라간다', () => {
  const f = (t: number) => 85 * 2.5 ** (t / 2);
  const tracker = new PitchTracker(SR / 6, { hopSec: 0.02 });
  const frames = tracker.push(decimate(synth(2, f), 6));
  const errs = frames.filter((x) => x.hz !== null).map((x) => Math.abs(hzToSt(x.hz as number, f(x.time))));
  assert.ok(errs.length > 80);
  assert.ok(median(errs) < 0.2, `median ${median(errs)}`);
});

test('한 옥타브 튄 값은 되돌린다', () => {
  const tracker = new PitchTracker(SR) as unknown as { smooth(hz: number | null, t: number): number | null };
  const out = [200, 201, 202, 404, 203, 102, 204].map((hz, i) => tracker.smooth(hz, i * 0.01) as number);
  assert.ok(out.every((hz) => hz > 180 && hz < 220));
});

test('곡선을 따라가면 모든 단계에서 통과한다', () => {
  for (const level of [1, 3, LEVELS.length]) {
    const { run } = play(level, follower);
    assert.equal(run.result.passed, true, `level ${level}: ${JSON.stringify(run.result)}`);
    assert.ok(run.success_rate_pct >= 70);
  }
});

test('한 높이로만 내면 통과하지 못한다', () => {
  for (const level of [1, 3, LEVELS.length]) {
    const { run } = play(level, monotone);
    assert.equal(run.result.passed, false, `level ${level}`);
  }
});

test('소리를 내지 않으면 판정 보류이고 별을 얻지 못한다', () => {
  const { run } = play(1, silent);
  assert.equal(run.decision, 'deferred');
  assert.equal(run.result.stars_collected, 0);
});

test('기준값이 실제 목소리와 4반음 달라도 첫 구절로 맞춰서 통과한다', () => {
  const { run, game } = play(1, follower, BASE, stToHz(-4, BASE.f0MedianHz));
  assert.ok(Math.abs(game.offsetSt + 4) < 0.5, `offset ${game.offsetSt}`);
  assert.equal(run.result.passed, true);
});

test('길을 한 번 벗어나 머물면 피격은 한 번만 센다', () => {
  const outside: Voice = (round, t) => {
    const p = phraseAt(round, t);
    if (!p) return null;
    if (p.index === 1) return (shapeAt(round, t) ?? 0) + 8;
    return p.warmup ? 0 : shapeAt(round, t - 0.15) ?? 0;
  };
  const { run } = play(1, outside);
  assert.ok(run.result.hits >= 1 && run.result.hits <= 2, `hits ${run.result.hits}`);
});

test('구절 시작 0.5초는 판정하지 않는다', () => {
  const round = buildRound(1, undefined, 3);
  const p = round.phrases[1];
  const target = (t: number) => shapeAt(round, t);
  assert.notEqual(deviationFrom(target, p.start + 0.1, 99), null);
  const game = createGame(round, BASE);
  addPitchFrame(game, round, BASE, { time: 0, levelDbfs: -20, hz: 120, rawHz: 120, clarity: 1 }, p.start + 0.1);
  addPitchFrame(game, round, BASE, { time: 0, levelDbfs: -20, hz: 120, rawHz: 120, clarity: 1 }, p.start + 0.8);
  assert.deepEqual(game.frames.map((f) => f.scored), [false, true]);
});

test('라운드 기록이 api.md 7.2 형식이다', () => {
  const { run } = play(2, follower);
  assert.ok(isUuidV4(run.game_run_id));
  assert.equal(run.game, 'spaceship');
  assert.equal(run.round_index, 1);
  assert.match(run.started_at, /(Z|[+-]\d\d:\d\d)$/);
  assert.ok(run.success_rate_pct >= 0 && run.success_rate_pct <= 100);
  assert.ok(['up', 'down', 'hold', 'deferred'].includes(run.decision));
  assert.ok((run.decision_reason ?? '').length <= 500);
  assert.equal(typeof run.result.pitch_range_semitones, 'number');
  assert.ok(JSON.stringify(run).length < 10_000);
});
