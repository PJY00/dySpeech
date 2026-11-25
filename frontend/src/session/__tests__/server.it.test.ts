// 서버 통합 시험(환경 변수가 있을 때만 실행). 로컬 백엔드(테스트 DB + 실제 S3)나 배포 서버에 보낸다.
//   DYSPEECH_IT_URL      서버 주소(예: http://127.0.0.1:8000)
//   DYSPEECH_IT_WAIT_MS  서버 처리를 기다리는 최대 시간(밀리초, 기본 240000)
//   DYSPEECH_IT_KEY      앱 키(서버의 APP_API_KEY)
//   DYSPEECH_IT_CLEANUP  만든 사용자 ID를 적을 정리 목록 파일(backend/tools/cleanup.py 형식). 없으면 임시 폴더
// 실제 녹음과 같은 형식(모노·16비트·48 kHz WAV)의 합성 신호로 보내 done까지 확인한다.
// - 반복 측정(retest) 한 회차: 기기 값과 서버 값 비교
// - 훈련(training) 한 회차: 측정 블록 + 노래방 라운드 2개(라운드 녹음 1개), 진전도 기준선
// - 훈련 한 회차: 측정 블록 + 노래방 따라 읽기 게임의 판정 코드로 만든 라운드(합성 낭독 5문장)
// - 훈련 세 회차(값이 서로 다름): 게임 기준값이 최근 3회차 중앙값으로 나오고, 서버에는 그날 기기 값이 그대로 간다(이슈 #41)

import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

import { FsStore } from '../testing/fsStore';
import { completeAndWait, IT_KEY, IT_URL, itModule, rememberForCleanup, runMeasurementBlock } from '../testing/it';
import { analyzeSentence } from '../../games/guide_reading/analysis';
import { loadReadingState } from '../../games/guide_reading/reference';
import { buildGameRun, judgeSentence, targetFor } from '../../games/guide_reading/scoring';
import { roundSentences } from '../../games/guide_reading/sentences';
import { RATE, S1_SPEECH, speech } from '../testing/synth';
import type { BlockVariation } from '../testing/synth';
import { gameBaselines, median } from '../../training/gameTypes';
import { MemoryStore } from '../store';
import type { Baselines, GameRun } from '../types';
import { uuidv4 } from '../uuid';
import { encodeWav } from '../wav';

test('서버 통합 시험: 반복 측정(retest) 한 회차가 실제 S3 업로드를 거쳐 done이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
  const { dir, module } = await itModule('retest');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const session = await module.startSession('retest', { clientMeta: { source: 'integration_test' } });
  assert.equal(session.needsCalibration, true);
  await runMeasurementBlock(session, dir, 21);
  const p = await completeAndWait(session, '반복 측정');
  assert.equal(p.state, 'done', JSON.stringify(p));
  assert.equal(p.uploaded, 6);
  const server = new Map(p.server?.server_measurements.map((m) => [m.metric as string, m.value]));
  const b = session.baselines();
  assert.ok(b);
  console.log('[it] user', user.userId, 'session', session.id);
  console.log('[it] server', JSON.stringify(p.server?.server_measurements.map((m) => [m.metric, Math.round(m.value * 100) / 100, m.low_reliability])));
  // 기기 값과 서버 값 비교: 같은 대역(60 Hz~8 kHz)·같은 정의라 거의 같아야 한다
  const pairs: [string, number | null, number][] = [
    ['noise_floor', b.noiseFloorDbfs, 1],
    ['sustained_level', b.sustainedLevelDbfs, 0.5],
    ['max_phonation_time', b.maxPhonationTimeS, 0.05],
    ['articulation_rate', b.articulationRateSps, (server.get('articulation_rate') ?? 0) * 0.1],
    ['f0_median', b.f0MedianHz, (server.get('f0_median') ?? 0) * 0.03],
  ];
  for (const [metric, device, tol] of pairs) {
    const s = server.get(metric);
    assert.ok(s !== undefined && device !== null, `${metric}: 서버 ${s}, 기기 ${device}`);
    console.log(`[it] ${metric} 서버 ${s.toFixed(2)} 기기 ${device.toFixed(2)} 차이 ${(device - s).toFixed(2)}`);
    assert.ok(Math.abs(device - s) <= tol, `${metric} 차이 ${(device - s).toFixed(3)} > ${tol}`);
  }
  // 202 뒤 기기 복사 파일은 지운다
  const left = await new FsStore(dir).list('.');
  assert.deepEqual(left.filter((n) => n.endsWith('.wav')), []);
});

test('서버 통합 시험: 노래방 라운드가 들어간 훈련(training) 회차가 done이 되고 진전도 기준선이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
  const { dir, module } = await itModule('training');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const session = await module.startSession('training', { clientMeta: { source: 'integration_test' } });
  await runMeasurementBlock(session, dir, 33);
  // 노래방형 낭독(guide_reading) 라운드 2개. 둘째 라운드는 라운드 녹음(kind = game)도 올린다
  const runs: GameRun[] = [1, 2].map((round) => ({
    game_run_id: uuidv4(),
    game: 'guide_reading',
    level: round,
    round_index: round,
    started_at: new Date().toISOString(),
    params: { sentence_ids: ['g01', 'g02', 'g03', 'g04', 'g05'], target_rate_range: [3.6, 5.0], guide_rate: 4.3 },
    success_rate_pct: round === 1 ? 80 : 60,
    decision: round === 1 ? 'up' : 'hold',
    decision_reason: round === 1 ? '성공률 80% 이상' : '성공률 40~80%',
    result: { sentences: [{ sentence_id: 'g01', articulation_rate: 4.2, cv: 0.31, detected_syllables: 9, passed: true }] },
  }));
  for (const r of runs) await session.addGameRun(r);
  const reading = speech({ ...S1_SPEECH, syllables: 9, pauseAfter: [], seed: 41 });
  const path = join(dir, 'game-round-2.wav');
  await writeFile(path, encodeWav(reading.samples, RATE));
  await session.addRecording({ kind: 'game', gameRunId: runs[1].game_run_id, fileUri: pathToFileURL(path).href });
  const p = await completeAndWait(session, '노래방 라운드 훈련');
  assert.equal(p.state, 'done', JSON.stringify(p));
  assert.equal(p.uploaded, 7, '측정 녹음 6개 + 라운드 녹음 1개');
  assert.equal(p.server?.purpose, 'training');
  assert.equal(p.server?.is_baseline, true, '이 사용자의 첫 완료 훈련 회차');
  const history = await module.getHistory();
  assert.ok(history);
  const mpt = history.metrics.find((m) => m.metric === 'max_phonation_time');
  assert.equal(mpt?.baseline?.session_id, session.id);
  assert.equal(mpt?.series.length, 1);
  assert.equal(history.recent_device_measurements[0]?.session_id, session.id);
  console.log('[it] training user', user.userId, 'session', session.id, 'history statuses', JSON.stringify(history.metrics.map((m) => [m.metric, m.status])));
});

test('서버 통합 시험: 노래방 따라 읽기 게임 판정 코드로 만든 라운드가 든 훈련 회차가 done이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
  const { dir, module } = await itModule('guide');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const session = await module.startSession('training', { clientMeta: { source: 'integration_test' } });
  await runMeasurementBlock(session, dir, 45);
  const b = session.baselines();
  assert.ok(b?.articulationRateSps);
  // 게임과 같은 순서: 기준 말속도(측정 블록 조음속도) → 목표 → 문장마다 분석·판정 → 라운드 기록
  const target = targetFor(b.articulationRateSps, 1);
  const results = roundSentences(1).map((sent, i) => {
    const x = speech({ ...S1_SPEECH, syllables: sent.syllables, pauseAfter: [], leadS: 1, tailS: 1.2, seed: 50 + i });
    const a = analyzeSentence(x.samples, RATE, sent.syllables, { noiseFloorDbfs: b.noiseFloorDbfs, searchRange: b.f0SearchRangeHz });
    return judgeSentence(sent.id, sent.syllables, a, target);
  });
  const run = buildGameRun(
    { gameRunId: uuidv4(), level: 1, roundIndex: 1, startedAt: new Date().toISOString(), referenceRate: b.articulationRateSps, referenceSource: 'recent_median', target },
    results,
  );
  console.log('[it] guide_reading', JSON.stringify({ success_rate_pct: run.success_rate_pct, decision: run.decision, rates: results.map((r) => r.articulation_rate), cv: results.map((r) => r.cv), detected: results.map((r) => r.detected_syllables) }));
  assert.equal(run.decision, 'up', '정답과 같은 빠르기의 합성 낭독은 모두 통과');
  await session.addGameRun(run);
  const p = await completeAndWait(session, '게임 판정 라운드 훈련');
  assert.equal(p.state, 'done', JSON.stringify(p));
  assert.equal(p.server?.purpose, 'training');
});

test('서버 통합 시험: 훈련 3회차에서 게임 기준값이 최근 3회차 중앙값으로 나오고, 서버에는 그날 기기 값이 그대로 간다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 900_000 }, async () => {
  const { dir, module } = await itModule('median');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const readingStore = new MemoryStore();
  // 회차마다 "아" 길이·음량, 낭독 빠르기·음높이를 다르게 만든다: 보통 → 길고 작고 느리고 높게 → 짧고 크고 빠르고 낮게
  const days: { seed: number; vary: BlockVariation }[] = [
    { seed: 61, vary: {} },
    { seed: 62, vary: { trialS: [9, 10, 9.5], sustainedDbfs: -26, sentence: { vowelS: S1_SPEECH.vowelS.map((v) => v * 1.3), f0StartHz: 190, f0EndHz: 150 } } },
    { seed: 63, vary: { trialS: [4, 5, 4.5], sustainedDbfs: -16, sentence: { vowelS: S1_SPEECH.vowelS.map((v) => v * 0.85), f0StartHz: 120, f0EndHz: 95 } } },
  ];
  const measured: Baselines[] = [];
  const sessionIds: string[] = [];
  const picks: [string, (b: Baselines) => number | null][] = [
    ['sustainedLevelDbfs', (b) => b.sustainedLevelDbfs],
    ['f0MedianHz', (b) => b.f0MedianHz],
    ['maxPhonationTimeS', (b) => b.maxPhonationTimeS],
    ['articulationRateSps', (b) => b.articulationRateSps],
  ];
  for (const [i, day] of days.entries()) {
    const session = await module.startSession('training', { clientMeta: { source: 'integration_test' } });
    // 앱과 같은 순서: 회차를 열 때 지난 훈련 회차 값을 읽어 두고, 측정 블록이 끝나면 게임 기준값을 계산한다
    const earlier = await module.listTrainingBaselines();
    assert.equal(earlier.length, i, '앞 회차 수');
    await runMeasurementBlock(session, dir, day.seed, day.vary);
    const today = session.baselines();
    assert.ok(today);
    measured.push(today);
    sessionIds.push(session.id);
    const game = gameBaselines(today, earlier);
    for (const [name, pick] of picks) {
      const values = measured.map(pick);
      assert.ok(values.every((v) => v !== null), `${name}: 회차마다 값이 있어야 함 ${JSON.stringify(values)}`);
      assert.equal(pick(game as unknown as Baselines), median(values as number[]), `${i + 1}회차 ${name}`);
    }
    assert.equal(game.noiseFloorDbfs, today.noiseFloorDbfs, '잡음 바닥은 그날 값');
    assert.deepEqual(game.f0SearchRangeHz, today.f0SearchRangeHz, '탐색 범위는 그날 값(첫 회차 캘리브레이션)');
    console.log(`[it] ${i + 1}회차 그날 값`, JSON.stringify(picks.map(([n, pick]) => [n, pick(today)])), '게임 기준값', JSON.stringify(picks.map(([n, pick]) => [n, pick(game as unknown as Baselines)])));

    // 속도 맞춰 읽기: 넘겨받은 중앙값이 기준 말속도가 되고, 그 값으로 만든 라운드 기록을 서버가 받는다
    const state = await loadReadingState(readingStore, user.userId, game.articulationRateSps);
    assert.deepEqual([state.referenceRate, state.source], [game.articulationRateSps, 'recent_median']);
    const target = targetFor(state.referenceRate, 1);
    const results = roundSentences(1).map((sent, k) => {
      const x = speech({ ...S1_SPEECH, syllables: sent.syllables, pauseAfter: [], leadS: 1, tailS: 1.2, seed: 70 + i * 10 + k });
      const a = analyzeSentence(x.samples, RATE, sent.syllables, { noiseFloorDbfs: today.noiseFloorDbfs, searchRange: today.f0SearchRangeHz });
      return judgeSentence(sent.id, sent.syllables, a, target);
    });
    const run = buildGameRun(
      { gameRunId: uuidv4(), level: 1, roundIndex: 1, startedAt: new Date().toISOString(), referenceRate: state.referenceRate, referenceSource: state.source, target },
      results,
    );
    await session.addGameRun(run);
    const p = await completeAndWait(session, `중앙값 ${i + 1}회차`);
    assert.equal(p.state, 'done', JSON.stringify(p));
  }
  // 회차마다 값이 실제로 달랐는지(같으면 중앙값 시험이 뜻이 없다)
  for (const [name, pick] of picks) {
    const values = measured.map(pick) as number[];
    assert.equal(new Set(values.map((v) => v.toFixed(2))).size, 3, `${name}: 세 회차 값이 서로 달라야 함 ${JSON.stringify(values)}`);
  }
  // 서버가 받은 기기 지표는 중앙값이 아니라 그 회차의 값 그대로다
  const history = await module.getHistory();
  assert.ok(history);
  assert.deepEqual(history.recent_device_measurements.map((r) => r.session_id), sessionIds, '최근 3회차, 오래된 회차부터');
  for (const [k, row] of history.recent_device_measurements.entries()) {
    const sent = new Map(row.measurements.map((m) => [m.metric as string, m.value]));
    const pairs: [string, number | null][] = [
      ['sustained_level', measured[k].sustainedLevelDbfs],
      ['f0_median', measured[k].f0MedianHz],
      ['max_phonation_time', measured[k].maxPhonationTimeS],
      ['articulation_rate', measured[k].articulationRateSps],
    ];
    for (const [metric, device] of pairs) {
      const value = sent.get(metric);
      assert.ok(value !== undefined && device !== null, `${k + 1}회차 ${metric}`);
      assert.ok(Math.abs(value - device) <= 0.06, `${k + 1}회차 ${metric}: 서버가 받은 값 ${value}, 그날 기기 값 ${device}`);
    }
  }
  console.log('[it] median user', user.userId, 'sessions', JSON.stringify(sessionIds));
});
