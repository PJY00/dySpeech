import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiClient } from '../api';
import { createSessionModule, RecordingRejected, RETRY_DELAYS_MS } from '../core';
import type { Session, SessionModule } from '../core';
import { MemoryStore } from '../store';
import { FAKE_KEY, FakeServer } from '../testing/fakeServer';
import { uuidv4 } from '../uuid';
import { encodeWav } from '../wav';

interface Rig {
  server: FakeServer;
  store: MemoryStore;
  sleeps: number[];
  module: SessionModule;
}

function rig(options: { store?: MemoryStore; server?: FakeServer; appKey?: string; configured?: boolean; recordingsDir?: string } = {}): Rig {
  const server = options.server ?? new FakeServer();
  const store = options.store ?? new MemoryStore();
  const sleeps: number[] = [];
  const module = createSessionModule({
    store,
    api: new ApiClient({ baseUrl: 'http://api.fake', appKey: options.appKey ?? FAKE_KEY, fetch: server.fetch }),
    apiConfigured: options.configured ?? true,
    device: () => ({ device_model: 'SM-TEST', os_version: 'Android 16', app_version: '1.0.0' }),
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    log: () => {},
    recordingsDir: options.recordingsDir,
  });
  return { server, store, sleeps, module };
}

let fileNo = 0;
function wavFile(store: MemoryStore, seconds = 0.5, options: { rate?: number; bytes?: Uint8Array } = {}): string {
  const uri = `file:///rec/${++fileNo}.wav`;
  const rate = options.rate ?? 48000;
  store.files.set(uri, options.bytes ?? encodeWav(new Float32Array(Math.round(seconds * rate)).fill(0.01), rate));
  return uri;
}

/** 반복 측정 한 회차: noise_floor, mpt_trial 2개, eval_sentence */
async function measurementSession(r: Rig): Promise<Session> {
  const s = await r.module.startSession('retest');
  await s.addRecording({ kind: 'noise_floor', fileUri: wavFile(r.store, 2.5) });
  await s.addRecording({ kind: 'mpt_trial', trialNo: 1, fileUri: wavFile(r.store, 5) });
  await s.addRecording({ kind: 'mpt_trial', trialNo: 2, fileUri: wavFile(r.store, 6) });
  await s.addRecording({ kind: 'eval_sentence', sentenceId: 's1', fileUri: wavFile(r.store, 4) });
  await s.setDeviceMeasurements([{ metric: 'noise_floor', value: -60, unit: 'dBFS' }]);
  return s;
}

const s3Puts = (r: Rig) => r.server.log.filter((x) => x.method === 'PUT' && x.url.startsWith('https://s3.fake/'));

test('정상 흐름: 등록 → 업로드 URL → S3 → complete, 202 뒤 기기 WAV를 지우고 done까지 확인한다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  assert.equal(s.status().state, 'recording');
  await s.complete({ clientMeta: { note: 'test' } });
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.equal(s.status().uploaded, 4);
  assert.equal(r.store.files.size, 0, '202 뒤 기기 복사 파일 삭제');
  const fs = r.server.sessions.get(s.id);
  assert.ok(fs?.complete);
  assert.equal(fs.body.purpose, 'retest');
  assert.equal(fs.body.sample_rate, 48000);
  assert.equal(fs.body.input_device, 'unknown');
  assert.deepEqual(fs.complete.device_measurements, [{ metric: 'noise_floor', value: -60, unit: 'dBFS', low_reliability: false }]);
  assert.deepEqual(fs.complete.client_meta, { note: 'test' });
  const kinds = [...fs.recordings.values()].map((x) => [x.kind, x.trial_no ?? x.sentence_id ?? null]);
  assert.deepEqual(kinds, [
    ['noise_floor', null],
    ['mpt_trial', 1],
    ['mpt_trial', 2],
    ['eval_sentence', 's1'],
  ]);
  const first = [...fs.recordings.values()][0];
  assert.equal(first.duration_s, 2.5);
  assert.equal(first.size_bytes, 44 + 2.5 * 48000 * 2);
  r.server.finish(s.id, 'done');
  const p = await s.waitForResult({ intervalMs: 1 });
  assert.equal(p.state, 'done');
  assert.equal(p.server?.server_measurements.length, 1);
});

test('네트워크 끊김·500은 2초, 5초 간격으로 다시 보낸다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  r.server.fail('POST', /\/complete$/, 1, { kind: 'network' });
  r.server.fail('POST', /\/complete$/, 1, { kind: 'status', status: 500, body: { error: { code: 'INTERNAL_ERROR', message: '' } } });
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.deepEqual(r.sleeps, [2000, 5000]);
  assert.equal(r.server.sessions.get(s.id)?.completeCalls, 1);
});

test('5번 다시 보내도 안 되면 queued로 두고, 다음 계기에 이어서 보낸다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  r.server.fail('POST', /\/upload-urls$/, 6, { kind: 'network' });
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'queued');
  assert.equal(s.status().lastError?.code, 'NETWORK');
  assert.deepEqual(r.sleeps, RETRY_DELAYS_MS);
  assert.equal(r.store.files.size, 4, '보내기 전에는 기기의 복사 파일을 남긴다');
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.equal(s.status().lastError, null);
  assert.equal(r.server.count('PUT', /\/v1\/sessions\//), 1, '세션 등록은 한 번이면 된다');
});

test('409 MISSING_RECORDINGS: 빠진 녹음만 다시 올리고 complete를 다시 보낸다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  r.server.dropNextUploads = 1;
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.equal(r.server.dropped.length, 1);
  assert.equal(r.server.sessions.get(s.id)?.completeCalls, 2);
  const dropped = r.server.dropped[0];
  assert.equal(s3Puts(r).filter((x) => x.url.includes(dropped)).length, 2, '빠진 녹음만 한 번 더 올린다');
  assert.equal(s3Puts(r).length, 5);
});

test('S3 403(URL 만료): 남은 녹음의 업로드 URL을 다시 받아 올린다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  r.server.fail('PUT', /^https:\/\/s3\.fake\//, 1, { kind: 's3', status: 403, code: 'AccessDenied' });
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.equal(r.server.count('POST', /\/upload-urls$/), 2);
});

test('404: 세션이 없으면 세션 등록부터, 사용자가 없으면 사용자 등록부터 다시 한다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  r.server.fail('POST', /\/complete$/, 1, { kind: 'status', status: 404, body: { error: { code: 'SESSION_NOT_FOUND', message: '' } } });
  r.server.fail('PUT', /\/v1\/sessions\//, 1, { kind: 'status', status: 404, body: { error: { code: 'USER_NOT_FOUND', message: '' } } });
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'processing');
  assert.equal(r.server.count('PUT', /\/v1\/users\//), 2);
  assert.equal(r.server.count('PUT', /\/v1\/sessions\//), 3);
});

test('401·422 VALIDATION_ERROR는 다시 보내지 않고 blocked로 두며 이유를 남긴다', async () => {
  const bad = rig({ appKey: 'wrong' });
  const s = await measurementSession(bad);
  await s.complete();
  await bad.module.syncNow();
  assert.equal(s.status().state, 'blocked');
  assert.equal(s.status().lastError?.code, 'INVALID_APP_KEY');
  assert.deepEqual(bad.sleeps, []);

  const r = rig();
  const t = await measurementSession(r);
  const fields = [{ path: 'body.device_measurements[0].unit', reason: 'x' }];
  r.server.fail('POST', /\/complete$/, 9, { kind: 'status', status: 422, body: { error: { code: 'VALIDATION_ERROR', message: '요청이 API 명세와 다릅니다', fields } } });
  await t.complete();
  await r.module.syncNow();
  assert.equal(t.status().state, 'blocked');
  assert.match(t.status().lastError?.message ?? '', /body\.device_measurements\[0\]\.unit/);
  assert.equal(r.server.sessions.get(t.id)?.completeCalls ?? 0, 0);
});

test('앱을 다시 켜면: 보내다 멈춘 세션을 이어서 보내고, complete 전 세션은 unfinished로 돌려준다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const a = rig({ store, server });
  const sent = await measurementSession(a);
  server.fail('POST', /\/upload-urls$/, 6, { kind: 'network' });
  await sent.complete();
  await a.module.syncNow();
  assert.equal(sent.status().state, 'queued');
  const open = await a.module.startSession('training');
  await open.addRecording({ kind: 'noise_floor', fileUri: wavFile(store, 2) });
  // 보내는 중에 꺼진 경우를 흉내: 저장 문서를 sending으로 바꿔 둔다
  const doc = (await store.readJson(`sessions/${sent.id}.json`)) as { state: string };
  doc.state = 'sending';
  await store.writeJson(`sessions/${sent.id}.json`, doc);

  const b = rig({ store, server });
  const { resumed, unfinished } = await b.module.resumePending();
  assert.equal(resumed, 1);
  assert.deepEqual(unfinished.map((u) => u.id), [open.id]);
  await b.module.syncNow();
  const again = await b.module.openSession(sent.id);
  assert.equal(again?.status().state, 'processing');
  assert.equal(server.sessions.size, 1, '같은 세션 ID로 한 번만 등록');
  assert.equal(server.users.size, 1);

  await unfinished[0].complete();
  await b.module.syncNow();
  assert.equal(unfinished[0].status().state, 'processing');
  assert.equal(server.sessions.size, 2);
});

test('규격과 다른 WAV는 RecordingRejected로 거부하고 세션에 넣지 않는다', async () => {
  const r = rig();
  const s = await r.module.startSession('training');
  const stereo = encodeWav(new Float32Array(9600), 48000);
  new DataView(stereo.buffer).setUint16(22, 2, true);
  await assert.rejects(s.addRecording({ kind: 'noise_floor', fileUri: wavFile(r.store, 0, { bytes: stereo }) }), (e: unknown) => {
    assert.ok(e instanceof RecordingRejected);
    assert.match(e.problems[0], /채널이 2개/);
    return true;
  });
  await assert.rejects(s.addRecording({ kind: 'noise_floor', fileUri: wavFile(r.store, 0.5, { rate: 22050 }) }), RecordingRejected);
  assert.equal(s.status().total, 0);
});

test('개발용 새 사용자: 세션 등록·complete의 client_meta에 dev: true, ID는 정리 목록에 남는다', async () => {
  const r = rig();
  const first = await r.module.currentUser();
  assert.equal(first.dev, false);
  const dev = await r.module.startNewDevUser();
  assert.notEqual(dev.userId, first.userId);
  assert.deepEqual(await r.module.devUserIds(), [dev.userId]);
  const s = await measurementSession(r);
  assert.equal(s.userId, dev.userId);
  await s.complete();
  await r.module.syncNow();
  const fs = r.server.sessions.get(s.id);
  assert.equal((fs?.body.client_meta as Record<string, unknown>).dev, true);
  assert.equal((fs?.complete?.client_meta as Record<string, unknown>).dev, true);
});

test('첫 회차만 캘리브레이션이 필요하고, 기준값은 사용자에 저장된다', async () => {
  const r = rig();
  const s = await r.module.startSession('training');
  assert.equal(s.needsCalibration, true);
  const baselines = {
    sessionId: s.id,
    measuredAt: '2026-11-07T00:00:00.000Z',
    noiseFloorDbfs: -60,
    sustainedLevelDbfs: -20,
    f0MedianHz: 120,
    maxPhonationTimeS: 7.5,
    articulationRateSps: 5,
    f0RangeHz: { min: 116, max: 300 },
    f0SearchRangeHz: { min: 75, max: 360 },
  };
  await s.setBaselines(baselines, { calibration: { min: 116, max: 300 } });
  assert.deepEqual(await r.module.getLatestBaselines(), baselines);
  const next = await r.module.startSession('training');
  assert.equal(next.needsCalibration, false);
  assert.deepEqual(next.calibration(), { min: 116, max: 300 });
  assert.equal((await r.module.startSession('agc_test')).needsCalibration, false);
});

test('기기 지표: 단위가 다르면 거부하고, 같은 지표는 마지막 값 하나만 남긴다', async () => {
  const r = rig();
  const s = await r.module.startSession('training');
  await assert.rejects(s.setDeviceMeasurements([{ metric: 'f0_median', value: 1, unit: 'dBFS' }]), /Hz/);
  await s.setDeviceMeasurements([
    { metric: 'f0_median', value: 100, unit: 'Hz' },
    { metric: 'f0_median', value: 120, unit: 'Hz', low_reliability: true },
    { metric: 'noise_floor', value: Number.NaN, unit: 'dBFS' },
  ]);
  await s.complete();
  await r.module.syncNow();
  assert.deepEqual(r.server.sessions.get(s.id)?.complete?.device_measurements, [
    { metric: 'f0_median', value: 120, unit: 'Hz', low_reliability: true },
  ]);
});

test('게임 라운드: 같은 ID는 덮어쓰고, game 녹음은 그 라운드 ID로 올린다', async () => {
  const r = rig();
  const s = await r.module.startSession('training');
  const id = uuidv4();
  const run = { game_run_id: id, game: 'guide_reading', level: 1, round_index: 1, started_at: '2026-11-07T00:00:00Z', success_rate_pct: 60, decision: 'hold' as const };
  await s.addGameRun(run);
  await s.addGameRun({ ...run, success_rate_pct: 80, decision: 'up' });
  await assert.rejects(s.addGameRun({ ...run, game_run_id: 'x' }), /UUID v4/);
  await assert.rejects(s.addGameRun({ ...run, success_rate_pct: 800 }), /0~100/);
  await s.addRecording({ kind: 'game', gameRunId: id, fileUri: wavFile(r.store, 3) });
  await s.complete();
  await r.module.syncNow();
  const fs = r.server.sessions.get(s.id);
  assert.deepEqual((fs?.complete?.game_runs as { success_rate_pct: number }[]).map((g) => g.success_rate_pct), [80]);
  assert.equal([...(fs?.recordings.values() ?? [])][0].game_run_id, id);
  await assert.rejects(s.addGameRun(run), /complete 뒤/);
});

test('discard: complete 전 세션과 녹음 파일을 지운다', async () => {
  const r = rig();
  const s = await r.module.startSession('training');
  await s.addRecording({ kind: 'noise_floor', fileUri: wavFile(r.store, 2) });
  await s.discard();
  assert.equal(r.store.files.size, 0);
  assert.deepEqual(await r.module.listSessions(), []);
});

test('API 주소·앱 키가 없으면 보내지 않고 CONFIG_MISSING으로 기다린다', async () => {
  const r = rig({ configured: false });
  const s = await measurementSession(r);
  await s.complete();
  await r.module.syncNow();
  assert.equal(s.status().state, 'queued');
  assert.equal(s.status().lastError?.code, 'CONFIG_MISSING');
  assert.equal(r.server.log.length, 0);
});

test('서버 계산 실패(failed)는 오류 코드와 함께 돌려준다', async () => {
  const r = rig();
  const s = await measurementSession(r);
  await s.complete();
  await r.module.syncNow();
  r.server.finish(s.id, 'failed');
  const p = await s.waitForResult({ intervalMs: 1 });
  assert.equal(p.state, 'failed');
  assert.equal(p.server?.error?.code, 'PROCESSING_FAILED');
});

test('처리 중이 오래가면 기다리기를 멈추고 processing으로 돌려준다', async () => {
  let clock = 0;
  const server = new FakeServer();
  const store = new MemoryStore();
  const module = createSessionModule({
    store,
    api: new ApiClient({ baseUrl: 'http://api.fake', appKey: FAKE_KEY, fetch: server.fetch }),
    apiConfigured: true,
    device: () => ({ device_model: 'm', os_version: 'o', app_version: 'a' }),
    now: () => new Date(clock),
    sleep: async (ms) => {
      clock += ms;
    },
    log: () => {},
  });
  const s = await module.startSession('retest');
  await s.addRecording({ kind: 'noise_floor', fileUri: wavFile(store, 2) });
  await s.complete();
  const seen: string[] = [];
  const p = await s.waitForResult({ intervalMs: 4000, timeoutMs: 12000, onProgress: (x) => seen.push(x.state) });
  assert.equal(p.state, 'processing');
  assert.equal(clock, 12000);
  assert.equal(server.count('GET', /\/v1\/sessions\//), 4);
  assert.ok(seen.every((x) => x === 'processing'));
});

test('앱 시작 정리: 어느 세션에도 없는 WAV(녹음 도중 꺼져 남은 파일)만 지운다', async () => {
  const store = new MemoryStore();
  const a = rig({ store, recordingsDir: 'file:///rec' });
  const s = await a.module.startSession('training');
  const kept = wavFile(store, 2);
  await s.addRecording({ kind: 'noise_floor', fileUri: kept });
  store.files.set('file:///rec/orphan.wav', new Uint8Array(10));
  store.files.set('file:///rec/note.txt', new Uint8Array(1));
  store.files.set('file:///other/elsewhere.wav', new Uint8Array(1));
  const b = rig({ store, recordingsDir: 'file:///rec' });
  const { sweptFiles, unfinished } = await b.module.resumePending();
  assert.equal(sweptFiles, 1);
  assert.ok(!store.files.has('file:///rec/orphan.wav'));
  assert.ok(store.files.has(kept), '세션에 들어간 녹음은 남긴다');
  assert.ok(store.files.has('file:///rec/note.txt'));
  assert.ok(store.files.has('file:///other/elsewhere.wav'), '녹음 폴더 밖은 건드리지 않는다');
  assert.equal(unfinished.length, 1);
  // URI 표기가 달라도(file:// 뒤 / 개수, 퍼센트 인코딩) 같은 파일로 본다
  store.files.set('file:///rec/%ED%95%9C.wav', new Uint8Array(1));
  const t = await b.module.startSession('training');
  store.files.set('file:///rec/한.wav', encodeWav(new Float32Array(4800), 48000));
  await t.addRecording({ kind: 'noise_floor', fileUri: 'file:///rec/한.wav' });
  store.files.delete('file:///rec/한.wav');
  const c = rig({ store, recordingsDir: 'file:///rec' });
  assert.equal((await c.module.resumePending()).sweptFiles, 0);
});

test('complete에 끝난 시각을 주면 그 시각을 ended_at으로 보낸다(지난 날 회차를 다음 날 끝낼 때)', async () => {
  const r = rig();
  const s = await measurementSession(r);
  const endedAt = '2026-11-10T21:30:00.000Z';
  await s.complete({ endedAt });
  await s.waitForResult();
  assert.equal(r.server.sessions.get(s.id)?.complete?.ended_at, endedAt);
});
