// 게임 기준값: 오늘을 포함한 최근 3회차 측정값의 중앙값(이슈 #41).
// - 대상: "아" 음량, 기본주파수 중앙값, 최대발성지속시간, 조음속도
// - 잡음 바닥과 기본주파수 탐색 범위는 오늘 값 그대로
// - 지난 회차 값은 기기에 남은 훈련 회차 문서에서 읽는다(세션 모듈의 listTrainingBaselines)

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiClient } from '../../session/api';
import { createSessionModule } from '../../session/core';
import { MemoryStore } from '../../session/store';
import { FAKE_KEY, FakeServer } from '../../session/testing/fakeServer';
import type { Baselines } from '../../session/types';
import { gameBaselines, median, RECENT_SESSIONS } from '../gameTypes';

/** day일에 잰 기준값. values로 일부 값을 바꾼다 */
function measured(day: number, values: Partial<Baselines> = {}): Baselines {
  return {
    sessionId: `s${day}`,
    measuredAt: `2026-11-${String(day).padStart(2, '0')}T09:00:00.000Z`,
    noiseFloorDbfs: -60 - day,
    sustainedLevelDbfs: -30,
    f0MedianHz: 150,
    maxPhonationTimeS: 10,
    articulationRateSps: 5,
    f0RangeHz: null,
    f0SearchRangeHz: { min: 75 + day, max: 500 },
    ...values,
  };
}

test('중앙값: 홀수 개는 가운데 값, 짝수 개는 가운데 두 값의 평균, 없으면 null', () => {
  assert.equal(median([]), null);
  assert.equal(median([4]), 4);
  assert.equal(median([6, 4]), 5);
  assert.equal(median([9, 1, 5]), 5);
  assert.equal(median([8, 2, 4, 6]), 5);
  const values = [3, 1, 2];
  median(values);
  assert.deepEqual(values, [3, 1, 2], '받은 배열의 순서를 바꾸지 않는다');
});

test('기준값: 1회차는 오늘 값, 2회차는 두 값의 평균, 3회차부터는 최근 3회차의 중앙값', () => {
  assert.equal(RECENT_SESSIONS, 3);
  const day1 = measured(1, { sustainedLevelDbfs: -30, f0MedianHz: 150, maxPhonationTimeS: 10, articulationRateSps: 5 });
  const day2 = measured(2, { sustainedLevelDbfs: -26, f0MedianHz: 170, maxPhonationTimeS: 14, articulationRateSps: 7 });
  const day3 = measured(3, { sustainedLevelDbfs: -40, f0MedianHz: 160, maxPhonationTimeS: 4, articulationRateSps: 6 });
  const day4 = measured(4, { sustainedLevelDbfs: -20, f0MedianHz: 100, maxPhonationTimeS: 30, articulationRateSps: 9 });

  const first = gameBaselines(day1);
  assert.deepEqual(
    [first.sustainedLevelDbfs, first.f0MedianHz, first.maxPhonationTimeS, first.articulationRateSps],
    [-30, 150, 10, 5],
    '1회차: 오늘 값 그대로',
  );

  const second = gameBaselines(day2, [day1]);
  assert.deepEqual(
    [second.sustainedLevelDbfs, second.f0MedianHz, second.maxPhonationTimeS, second.articulationRateSps],
    [-28, 160, 12, 6],
    '2회차: 두 값의 평균',
  );

  const third = gameBaselines(day3, [day1, day2]);
  assert.deepEqual(
    [third.sustainedLevelDbfs, third.f0MedianHz, third.maxPhonationTimeS, third.articulationRateSps],
    [-30, 160, 10, 6],
    '3회차: 세 값의 중앙값. 오늘 값이 튀어도(-40 dBFS, 4초) 그대로 들어가지 않는다',
  );

  const fourth = gameBaselines(day4, [day1, day2, day3]);
  assert.deepEqual(
    [fourth.sustainedLevelDbfs, fourth.f0MedianHz, fourth.maxPhonationTimeS, fourth.articulationRateSps],
    [-26, 160, 14, 7],
    '4회차: 2·3·4회차만 쓴다(1회차는 빠짐)',
  );
});

test('기준값: 지난 회차의 순서는 상관없고, 오늘 회차가 섞여 있으면 뺀다', () => {
  const day1 = measured(1, { maxPhonationTimeS: 100 });
  const day2 = measured(2, { maxPhonationTimeS: 8 });
  const day3 = measured(3, { maxPhonationTimeS: 12 });
  const day4 = measured(4, { maxPhonationTimeS: 10 });
  assert.equal(gameBaselines(day4, [day1, day3, day2]).maxPhonationTimeS, 10, '새 회차부터 2개(3·2회차)만 고른다');
  assert.equal(gameBaselines(day4, [day2, day1, day3]).maxPhonationTimeS, 10);
  assert.equal(gameBaselines(day4, [day4, day3, day2]).maxPhonationTimeS, 10, '오늘 회차를 두 번 세지 않는다');
});

test('기준값: 잡음 바닥과 기본주파수 탐색 범위는 오늘 값 그대로', () => {
  const b = gameBaselines(measured(3), [measured(1), measured(2)]);
  assert.equal(b.noiseFloorDbfs, -63);
  assert.deepEqual(b.f0SearchRangeHz, { min: 78, max: 500 });
  assert.equal(gameBaselines(measured(3, { noiseFloorDbfs: null }), [measured(1), measured(2)]).noiseFloorDbfs, null, '오늘 잡음 바닥이 없으면 지난 값으로 채우지 않는다');
});

test('기준값: 최근 3회차 안에서 값이 있는 것만으로 구하고, 하나도 없으면 null', () => {
  const day1 = measured(1, { f0MedianHz: 140, articulationRateSps: 4 });
  const day2 = measured(2, { f0MedianHz: null, articulationRateSps: 6 });
  const day3 = measured(3, { f0MedianHz: 180, articulationRateSps: null });
  const third = gameBaselines(day3, [day1, day2]);
  assert.equal(third.f0MedianHz, 160, '값이 둘이면 두 값의 평균');
  assert.equal(third.articulationRateSps, 5, '오늘 값이 없어도 지난 두 회차 값으로 구한다');

  const day4 = measured(4, { f0MedianHz: null });
  assert.equal(gameBaselines(day4, [day1, day2, day3]).f0MedianHz, 180, '3회차 안에 값이 하나면 그 값(1회차 값은 창 밖이라 쓰지 않음)');

  const none = { f0MedianHz: null };
  assert.equal(gameBaselines(measured(4, none), [measured(1), measured(2, none), measured(3, none)]).f0MedianHz, null, '창 안에 값이 없으면 null');
});

test('기준값: 이전 버전이 저장한 회차에 항목이 없어도(undefined) 값이 없는 것으로 본다', () => {
  const old = measured(1) as Partial<Baselines>;
  delete old.articulationRateSps;
  const b = gameBaselines(measured(2, { articulationRateSps: 6 }), [old as Baselines]);
  assert.equal(b.articulationRateSps, 6);
});

test('지난 회차 값: 현재 사용자의 훈련 회차만, 측정 블록을 끝낸 회차만, 새 회차부터. 보내지 못한 회차도 넣고 버린 회차는 뺀다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const make = () =>
    createSessionModule({
      store,
      api: new ApiClient({ baseUrl: 'http://api.fake', appKey: FAKE_KEY, fetch: server.fetch }),
      apiConfigured: false,
      device: () => ({ device_model: 'm', os_version: 'o', app_version: 'a' }),
      sleep: async () => {},
      log: () => {},
    });
  const app = make();
  assert.deepEqual(await app.listTrainingBaselines(), [], '회차가 없으면 빈 목록');

  const open = async (purpose: 'training' | 'retest', day: number, values: Partial<Baselines> = {}) => {
    const s = await app.startSession(purpose);
    await s.setBaselines({ ...measured(day, values), sessionId: s.id });
    return s;
  };
  const first = await open('training', 1, { maxPhonationTimeS: 8 });
  await first.complete();
  await open('retest', 2, { maxPhonationTimeS: 99 });
  const second = await open('training', 3, { maxPhonationTimeS: 12 });
  const thrown = await open('training', 4, { maxPhonationTimeS: 50 });
  await thrown.discard();
  await app.startSession('training');

  const list = await app.listTrainingBaselines();
  assert.deepEqual(list.map((b) => b.sessionId), [second.id, first.id], '반복 측정·버린 회차·측정 전 회차는 빠지고, 새 회차부터');
  assert.deepEqual(list.map((b) => b.maxPhonationTimeS), [12, 8]);

  // 앱을 다시 켜도(새 모듈, 같은 저장소) 기기에 남은 문서에서 읽는다
  const again = make();
  assert.deepEqual((await again.listTrainingBaselines()).map((b) => b.sessionId), [second.id, first.id]);

  // 세 번째 훈련 회차의 게임 기준값
  const today = { ...measured(5, { maxPhonationTimeS: 20 }), sessionId: 'today' };
  assert.equal(gameBaselines(today, await again.listTrainingBaselines()).maxPhonationTimeS, 12);

  // 다른 사용자(개발용 새 사용자)로 바꾸면 앞 사용자의 회차는 쓰지 않는다
  await again.startNewDevUser();
  assert.deepEqual(await again.listTrainingBaselines(), []);
});
