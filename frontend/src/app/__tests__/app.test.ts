import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiClient } from '../../session/api';
import { createSessionModule } from '../../session/core';
import { MemoryStore } from '../../session/store';
import { FAKE_KEY, FakeServer } from '../../session/testing/fakeServer';
import { encodeWav } from '../../session/wav';
import type { GameRun } from '../../session/types';
import { METRIC_UNITS } from '../../session/types';
import { parseDevScreen, resolveDevScreen, TEMP_BASELINES } from '../devScreen';
import { initialFlow, reduceFlow, totalRounds, unfinishedOptions } from '../flow';
import type { FlowAction, FlowState } from '../flow';
import { GAME_AREAS, GAME_NAMES } from '../gameTypes';
import { formatMeasurement, METRIC_LABELS } from '../labels';
import { applyDecision, LevelBook, LEVELS_DOC } from '../levels';

const run = (decision: GameRun['decision'], level = 2): GameRun => ({
  game_run_id: '6ecdaf48-7b9a-4fbc-936c-ba4b8e7fcd5a',
  game: 'guide_reading',
  level,
  round_index: 1,
  started_at: '2026-11-08T00:00:00Z',
  success_rate_pct: 80,
  decision,
});

const play = (actions: FlowAction[], from: FlowState = initialFlow()) => actions.reduce(reduceFlow, from);

test('오늘 훈련: 측정 블록 → 게임 선택 → 라운드 반복 → 끝 → 결과, 라운드 번호는 게임마다 센다', () => {
  let s = play([{ type: 'start', purpose: 'training' }]);
  assert.equal(s.screen.name, 'measure');
  assert.equal(s.purpose, 'training');
  s = play([{ type: 'measured' }, { type: 'pickGame', game: 'guide_reading', level: 2 }], s);
  assert.deepEqual(s.screen, { name: 'game', game: 'guide_reading', level: 2, roundIndex: 1 });
  s = play([{ type: 'roundEnded', run: run('up'), nextLevel: 3 }], s);
  assert.equal(s.screen.name, 'roundEnd');
  s = play([{ type: 'nextRound' }], s);
  assert.deepEqual(s.screen, { name: 'game', game: 'guide_reading', level: 3, roundIndex: 2 });
  s = play([{ type: 'roundEnded', run: run('hold', 3), nextLevel: 3 }, { type: 'chooseOther' }, { type: 'pickGame', game: 'balloon', level: 1 }], s);
  assert.deepEqual(s.screen, { name: 'game', game: 'balloon', level: 1, roundIndex: 1 });
  s = play([{ type: 'roundEnded', run: { ...run('down'), game: 'balloon' }, nextLevel: 1 }, { type: 'finish' }], s);
  assert.equal(s.screen.name, 'finishing');
  assert.equal(totalRounds(s), 3);
  s = play([{ type: 'finished' }], s);
  assert.equal(s.screen.name, 'result');
  assert.equal(play([{ type: 'home' }], s).screen.name, 'home');
});

test('반복 측정: 측정 블록 → 바로 끝내기(complete) → 결과. 게임 선택 없음', () => {
  const s = play([{ type: 'start', purpose: 'retest' }, { type: 'measured' }]);
  assert.equal(s.screen.name, 'finishing');
  assert.equal(play([{ type: 'finished' }], s).screen.name, 'result');
});

test('흐름에 맞지 않는 동작은 무시하고, 그만두기는 메인으로', () => {
  const home = initialFlow();
  assert.equal(reduceFlow(home, { type: 'measured' }), home);
  assert.equal(reduceFlow(home, { type: 'nextRound' }), home);
  assert.equal(reduceFlow(home, { type: 'finished' }), home);
  const measuring = play([{ type: 'start', purpose: 'training' }]);
  assert.equal(reduceFlow(measuring, { type: 'pickGame', game: 'balloon', level: 1 }), measuring, '측정 블록 전에는 게임을 열지 않는다');
  assert.equal(play([{ type: 'cancel' }], measuring).screen.name, 'home');
  assert.equal(play([{ type: 'unfinished' }]).screen.name, 'resume');
  assert.equal(play([{ type: 'unfinished' }], measuring), measuring);
  assert.equal(play([{ type: 'records' }]).screen.name, 'records');
  // 게임 선택에서 라운드 없이 끝낼 수 있다(등록된 게임이 없을 때)
  assert.equal(play([{ type: 'start', purpose: 'training' }, { type: 'measured' }, { type: 'finish' }]).screen.name, 'finishing');
});

test('단계: up +1, down −1, hold·deferred 그대로, 1~최고 단계 안', () => {
  assert.equal(applyDecision(2, 'up', 5), 3);
  assert.equal(applyDecision(5, 'up', 5), 5);
  assert.equal(applyDecision(1, 'down', 5), 1);
  assert.equal(applyDecision(3, 'down', 5), 2);
  assert.equal(applyDecision(3, 'hold', 5), 3);
  assert.equal(applyDecision(3, 'deferred', 5), 3);
});

test('단계 저장: 사용자·게임마다 따로 두고, 앱을 다시 켜도 남는다', async () => {
  const store = new MemoryStore();
  const book = new LevelBook(store);
  assert.equal(await book.get('u1', 'guide_reading'), 1);
  assert.equal(await book.record('u1', 'guide_reading', 1, 'up', 5), 2);
  await Promise.all([book.record('u1', 'balloon', 1, 'up', 3), book.record('u2', 'guide_reading', 4, 'down', 5)]);
  const again = new LevelBook(store);
  assert.equal(await again.get('u1', 'guide_reading'), 2);
  assert.equal(await again.get('u1', 'balloon'), 2);
  assert.equal(await again.get('u2', 'guide_reading'), 3);
  assert.ok(store.docs.has(LEVELS_DOC));
});

test('개발용 바로가기 EXPO_PUBLIC_SCREEN 해석', () => {
  assert.deepEqual(parseDevScreen(undefined), { kind: 'app' });
  assert.deepEqual(parseDevScreen('  '), { kind: 'app' });
  assert.deepEqual(parseDevScreen('measurement-test'), { kind: 'measurement-test' });
  assert.deepEqual(parseDevScreen('game-select'), { kind: 'game-select' });
  assert.deepEqual(parseDevScreen('game:guide_reading'), { kind: 'game', game: 'guide_reading' });
  assert.deepEqual(parseDevScreen('game:tetris'), { kind: 'unknown', value: 'game:tetris' });
  assert.equal(TEMP_BASELINES.f0SearchRangeHz.min, 75);
});

test('게임 이름은 api.md 7.2 형식(영문 소문자 snake_case), 영역은 호흡·말속도·억양', () => {
  for (const g of GAME_NAMES) assert.match(g, /^[a-z][a-z0-9_]*$/);
  assert.deepEqual(GAME_NAMES, ['balloon', 'candle', 'spaceship', 'roller_coaster', 'guide_reading']);
  assert.deepEqual(GAME_AREAS.map((a) => a.title), ['호흡', '말속도', '억양']);
});

test('지표 이름표가 11개 지표 모두에 있고, 값은 단위와 신뢰 낮음 표시를 붙인다', () => {
  assert.deepEqual(Object.keys(METRIC_LABELS).sort(), Object.keys(METRIC_UNITS).sort());
  assert.equal(formatMeasurement({ value: 12.345, unit: 's', low_reliability: false }), '12.35 초');
  assert.equal(formatMeasurement({ value: 4.1, unit: 'syllables/s', low_reliability: true }), '4.10 음절/초 (신뢰 낮음)');
  assert.equal(formatMeasurement({ value: 181.46, unit: 'Hz', low_reliability: false }), '181.5 Hz');
});

test('바로가기는 개발 빌드에서만: 릴리스는 값이 남아 있어도 앱 기본 화면', () => {
  for (const v of ['measurement-test', 'game-select', 'game:guide_reading', 'game:tetris', '', undefined]) {
    assert.deepEqual(resolveDevScreen(v, false), { kind: 'app' }, String(v));
  }
  assert.deepEqual(resolveDevScreen('game-select', true), { kind: 'game-select' });
  assert.deepEqual(resolveDevScreen('game:guide_reading', true), { kind: 'game', game: 'guide_reading' });
  assert.deepEqual(resolveDevScreen(undefined, true), { kind: 'app' });
});

test('끝나지 않은 회차: 측정 블록을 끝내지 못했으면 버리기만, 끝냈으면 보내기·버리기(훈련·반복 측정 같음)', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const make = () =>
    createSessionModule({
      store,
      api: new ApiClient({ baseUrl: 'http://api.fake', appKey: FAKE_KEY, fetch: server.fetch }),
      apiConfigured: true,
      device: () => ({ device_model: 'm', os_version: 'o', app_version: 'a' }),
      sleep: async () => {},
      log: () => {},
    });
  const a = make();
  const baselines = (sessionId: string) => ({
    sessionId,
    measuredAt: '2026-11-08T00:00:00.000Z',
    noiseFloorDbfs: -60,
    sustainedLevelDbfs: -20,
    f0MedianHz: 130,
    maxPhonationTimeS: 7,
    articulationRateSps: 4.5,
    f0RangeHz: null,
    f0SearchRangeHz: { min: 75, max: 500 },
  });
  const ids: Record<string, string> = {};
  for (const purpose of ['training', 'retest'] as const) {
    const half = await a.startSession(purpose);
    store.files.set(`file:///rec/${purpose}-noise.wav`, encodeWav(new Float32Array(48000), 48000));
    await half.addRecording({ kind: 'noise_floor', fileUri: `file:///rec/${purpose}-noise.wav` });
    ids[`${purpose}-half`] = half.id;
    const full = await a.startSession(purpose);
    await full.setBaselines(baselines(full.id));
    ids[`${purpose}-full`] = full.id;
  }
  // 앱을 다시 켠 것처럼 새 모듈로 읽는다
  const { unfinished } = await make().resumePending();
  assert.equal(unfinished.length, 4);
  for (const s of unfinished) {
    const o = unfinishedOptions(s);
    const full = s.id === ids[`${s.purpose}-full`];
    assert.equal(o.canSend, full, `${s.purpose} ${full ? '측정 끝냄' : '측정 못 끝냄'}`);
    assert.equal(o.reason, full ? null : '측정을 끝내지 못한 회차는 보낼 수 없어요');
  }
});
