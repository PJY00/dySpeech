// 로컬 백엔드 통합 시험(환경 변수가 있을 때만 실행).
//   DYSPEECH_IT_URL      서버 주소(예: http://127.0.0.1:8000). 로컬 테스트 DB + 실제 S3로 띄운 서버
//   DYSPEECH_IT_KEY      앱 키(서버의 APP_API_KEY)
//   DYSPEECH_IT_CLEANUP  만든 사용자 ID를 적을 정리 목록 파일(backend/tools/cleanup.py 형식). 없으면 임시 폴더
// 실제 녹음과 같은 형식(모노·16비트·48 kHz WAV)의 합성 신호로 보내 done까지 확인한다.
// - 반복 측정(retest) 한 회차: 기기 값과 서버 값 비교
// - 훈련(training) 한 회차: 측정 블록 + 노래방 라운드 2개(라운드 녹음 1개), 진전도 기준선

import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

import { FsStore } from '../testing/fsStore';
import { IT_KEY, IT_URL, itModule, rememberForCleanup, runMeasurementBlock } from '../testing/it';
import { RATE, S1_SPEECH, speech } from '../testing/synth';
import type { GameRun } from '../types';
import { uuidv4 } from '../uuid';
import { encodeWav } from '../wav';

test('로컬 백엔드: 반복 측정(retest) 한 회차가 실제 S3 업로드를 거쳐 done이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
  const { dir, module } = await itModule('retest');
  const user = await module.startNewDevUser();
  await rememberForCleanup(user.userId);
  const session = await module.startSession('retest', { clientMeta: { source: 'integration_test' } });
  assert.equal(session.needsCalibration, true);
  await runMeasurementBlock(session, dir, 21);
  await session.complete({ clientMeta: { source: 'integration_test' } });
  const p = await session.waitForResult({ intervalMs: 3000, timeoutMs: 240_000 });
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

test('로컬 백엔드: 노래방 라운드가 들어간 훈련(training) 회차가 done이 되고 진전도 기준선이 된다', { skip: !IT_URL || !IT_KEY ? 'DYSPEECH_IT_URL·DYSPEECH_IT_KEY 없음' : false, timeout: 300_000 }, async () => {
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
  await session.complete({ clientMeta: { source: 'integration_test' } });
  const p = await session.waitForResult({ intervalMs: 3000, timeoutMs: 240_000 });
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
