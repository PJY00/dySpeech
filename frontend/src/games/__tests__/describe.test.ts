import assert from 'node:assert/strict';
import { test } from 'node:test';

import { setLangValue } from '../../i18n/lang';
import type { GameRun } from '../../session/types';
import { describeBalloonRun } from '../balloon/describe';
import { describeGuideReadingRun } from '../guide_reading/describe';
import { describeRollerCoasterRun } from '../roller_coaster/describe';
import { describeSpaceshipRun } from '../spaceship/describe';

const base: GameRun = {
  game_run_id: 'r1',
  game: 'balloon',
  level: 2,
  round_index: 1,
  started_at: '2026-11-19T00:00:00.000Z',
  success_rate_pct: 100,
  decision: 'up',
  decision_reason: 'Goal reached - moving up a level',
};

function both(fn: () => string | null): [string | null, string | null] {
  setLangValue('ko');
  const ko = fn();
  setLangValue('en');
  const en = fn();
  setLangValue('ko');
  return [ko, en];
}

test('판정 한 줄은 서버로 가는 문구와 상관없이 화면 언어로만 나온다', () => {
  const balloon = { ...base, params: { target_hold_s: 1.9 }, result: { best_hold_s: 2.1, voiced_coverage_pct: 80 } };
  assert.deepEqual(both(() => describeBalloonRun(balloon)), ['가장 오래 유지 2.1초 / 목표 1.9초', 'Longest hold 2.1 s / goal 1.9 s']);

  const ship = { ...base, game: 'spaceship' as const, success_rate_pct: 72.4, result: { passed: true, voiced_coverage_pct: 90, stars_collected: 5, stars_total: 6 } };
  assert.deepEqual(both(() => describeSpaceshipRun(ship)), ['곡선 통과 · 길 안 72% · 별 5/6', 'Curve passed · 72% in the lane · stars 5/6']);
  const coaster = { ...ship, game: 'roller_coaster' as const, result: { ...ship.result, passed: false } };
  assert.deepEqual(both(() => describeRollerCoasterRun(coaster)), ['곡선 미통과 · 길 안 72% · 코인 5/6', 'Curve not passed · 72% in the lane · coins 5/6']);

  const reading = {
    ...base,
    game: 'guide_reading' as const,
    decision_reason: '통과 3/4, 성공률 40% 초과 80% 미만, 끝까지 읽지 못한 시도 1개 제외',
    result: {
      sentences: [
        { judged: true, passed: true },
        { judged: true, passed: true },
        { judged: true, passed: true },
        { judged: true, passed: false },
        { judged: false, passed: false },
      ],
    },
  };
  assert.deepEqual(both(() => describeGuideReadingRun(reading)), ['통과 3/4문장 · 끝까지 못 읽은 1개 제외', 'Passed 3/4 sentences · 1 incomplete excluded']);
});

test('판정을 미룬 라운드는 그 까닭을, 결과가 없으면 null', () => {
  const deferred = { ...base, decision: 'deferred' as const, result: { voiced_coverage_pct: 32.6 } };
  assert.deepEqual(both(() => describeBalloonRun(deferred)), [
    '소리가 들린 시간이 33%라 이번 라운드는 세지 않았어요',
    'Voice heard 33% of the time, so this round was not counted',
  ]);
  assert.equal(describeSpaceshipRun(base), null);
  assert.equal(describeGuideReadingRun(base), null);
});
