import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { Baselines, Purpose } from '../../session/types';
import { planUnfinished, reportedToday } from '../openSession';
import type { UnfinishedLike } from '../openSession';

const NOW = new Date(2026, 10, 12, 10, 0);

function session(id: string, purpose: Purpose, startedAt: Date, measuredAt: string | null): UnfinishedLike {
  const b = measuredAt ? ({ measuredAt } as Baselines) : null;
  return { id, purpose, startedAt: startedAt.toISOString(), baselines: () => b };
}

test('오늘 측정을 마친 훈련 회차는 이어서 하고, 지난 날 회차는 마지막 라운드 시각으로 끝내고, 나머지는 묻는다', () => {
  const today = session('today', 'training', new Date(2026, 10, 12, 8), '2026-11-11T23:10:00.000Z');
  const yesterday = session('yesterday', 'training', new Date(2026, 10, 11, 20), '2026-11-11T11:05:00.000Z');
  const notMeasured = session('half', 'training', new Date(2026, 10, 12, 9), null);
  const retest = session('retest', 'retest', new Date(2026, 10, 12, 7), '2026-11-11T22:30:00.000Z');
  const plan = planUnfinished([yesterday, notMeasured, today, retest], { sessionId: 'yesterday', rounds: { balloon: 2 }, lastActivityAt: '2026-11-11T11:30:00.000Z' }, NOW);
  assert.equal(plan.reopen?.id, 'today');
  assert.deepEqual(plan.close.map((c) => [c.session.id, c.endedAt]), [['yesterday', '2026-11-11T11:30:00.000Z']]);
  assert.deepEqual(plan.ask.map((s) => s.id).sort(), ['half', 'retest']);
});

test('지난 날 회차에 라운드 기록이 없으면 측정이 끝난 시각으로 끝낸다', () => {
  const yesterday = session('y', 'training', new Date(2026, 10, 11, 20), '2026-11-11T11:05:00.000Z');
  const plan = planUnfinished([yesterday], null, NOW);
  assert.equal(plan.close[0].endedAt, '2026-11-11T11:05:00.000Z');
});

test('오늘 끝낸 훈련 회차가 있으면 그날은 새 훈련 회차를 열지 않는다(반복 측정은 세지 않음)', () => {
  const at = (h: number) => new Date(2026, 10, 12, h).toISOString();
  assert.equal(reportedToday([{ purpose: 'training', state: 'processing', startedAt: at(8) }], NOW), true);
  assert.equal(reportedToday([{ purpose: 'training', state: 'recording', startedAt: at(8) }], NOW), false);
  assert.equal(reportedToday([{ purpose: 'retest', state: 'done', startedAt: at(8) }], NOW), false);
  assert.equal(reportedToday([{ purpose: 'training', state: 'done', startedAt: new Date(2026, 10, 11, 8).toISOString() }], NOW), false);
});
