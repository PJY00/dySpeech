import assert from 'node:assert/strict';
import { test } from 'node:test';

import { MemoryStore } from '../../session/store';
import { completedAreas, dayKey, PASS_SUCCESS_RATE_PCT, roundPassed, TodayBook } from '../progress';
import type { RoundSummary } from '../progress';

const round = (area: RoundSummary['area'], success_rate_pct: number, decision: RoundSummary['decision'] = 'hold'): RoundSummary => ({
  game: 'guide_reading',
  area,
  success_rate_pct,
  decision,
});

test('라운드 통과: 성공률 기준 이상이면 통과, 가장 높은 단계의 hold도 통과, deferred는 통과 아님', () => {
  assert.equal(roundPassed(round('rate', PASS_SUCCESS_RATE_PCT, 'up')), true);
  assert.equal(roundPassed(round('rate', 100, 'hold')), true);
  assert.equal(roundPassed(round('rate', PASS_SUCCESS_RATE_PCT - 1, 'hold')), false);
  assert.equal(roundPassed(round('rate', 100, 'deferred')), false);
});

test('오늘 완료 영역: 통과한 라운드가 있는 영역만, 같은 영역은 한 번', () => {
  const areas = completedAreas([
    round('rate', 100),
    round('rate', 90),
    round('breath', 40),
    round('intonation', 85),
  ]);
  assert.deepEqual(areas, ['rate', 'intonation']);
});

test('오늘 기록: 날짜가 바뀌면 빈 기록, 초기화하면 비움', async () => {
  const book = new TodayBook(new MemoryStore());
  const day1 = new Date(2026, 10, 11, 9);
  const day2 = new Date(2026, 10, 12, 9);
  await book.add(round('rate', 100), day1);
  await book.add(round('breath', 90), day1);
  assert.equal((await book.rounds(day1)).length, 2);
  assert.equal((await book.rounds(day2)).length, 0);
  await book.reset(day1);
  assert.equal((await book.rounds(day1)).length, 0);
  assert.equal(dayKey(day1), '2026-11-11');
});
