// 결과를 보낸 뒤 화면을 떠나거나 앱을 꺼도 보내기·결과 받기가 이어지는지(이슈 #23).
// - complete()는 queued로 저장하고, 보내기는 세션 모듈이 화면과 따로 돌린다
// - 앱이 앞으로 오면 syncNow, 다시 열면 resumePending이 이어서 보내고 서버 계산 중인 세션 상태를 갱신한다
// - 결과 화면을 떠나면 waitForResult(signal)만 멈추고 보내기는 계속한다

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiClient } from '../api';
import { createSessionModule } from '../core';
import type { Session, SessionModule } from '../core';
import { MemoryStore } from '../store';
import { FAKE_KEY, FakeServer } from '../testing/fakeServer';
import { encodeWav } from '../wav';

const device = () => ({ device_model: 'SM-TEST', os_version: 'Android 16', app_version: '1.0.0' });
const S3_PUT = /^https:\/\/s3\.fake\//;

/** 기다림을 손으로 풀어 주는 sleep(재시도 대기 중인 보내기를 멈춰 둘 때) */
function gatedSleep() {
  const gates: (() => void)[] = [];
  return {
    sleep: (_ms: number) => new Promise<void>((r) => gates.push(r)),
    release: () => gates.splice(0).forEach((r) => r()),
    waiting: () => gates.length,
  };
}

function makeModule(store: MemoryStore, server: FakeServer, options: { sleep?: (ms: number) => Promise<void>; uploadTimeoutMs?: number } = {}): SessionModule {
  return createSessionModule({
    store,
    api: new ApiClient({ baseUrl: 'http://api.fake', appKey: FAKE_KEY, fetch: server.fetch, uploadTimeoutMs: options.uploadTimeoutMs }),
    apiConfigured: true,
    device,
    sleep: options.sleep ?? (async () => {}),
    log: () => {},
  });
}

let fileNo = 0;
async function measured(module: SessionModule, store: MemoryStore): Promise<Session> {
  const s = await module.startSession('training');
  for (const [kind, seconds] of [['noise_floor', 2.5], ['mpt_trial', 5], ['mpt_trial', 6], ['eval_sentence', 4]] as const) {
    const uri = `file:///rec/c${++fileNo}.wav`;
    store.files.set(uri, encodeWav(new Float32Array(Math.round(seconds * 48000)).fill(0.01), 48000));
    if (kind === 'mpt_trial') await s.addRecording({ kind, trialNo: seconds === 5 ? 1 : 2, fileUri: uri });
    else if (kind === 'eval_sentence') await s.addRecording({ kind, sentenceId: 's1', fileUri: uri });
    else await s.addRecording({ kind, fileUri: uri });
  }
  return s;
}

const tick = () => new Promise<void>((r) => setTimeout(r, 5));
async function until(cond: () => boolean | Promise<boolean>, what: string) {
  for (let i = 0; i < 400; i++) {
    if (await cond()) return;
    await tick();
  }
  assert.fail(`기다렸지만 되지 않음: ${what}`);
}

test('complete는 queued로 저장하고 곧바로 돌아오며, 보내기는 화면과 따로 돈다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const gate = gatedSleep();
  const m = makeModule(store, server, { sleep: gate.sleep });
  const s = await measured(m, store);
  server.fail('POST', /\/upload-urls$/, 1, { kind: 'network' });
  await s.complete();
  const saved = (await store.readJson(`sessions/${s.id}.json`)) as { state: string; endedAt: string | null };
  assert.ok(saved.endedAt, '끝난 시각이 저장됨');
  await until(() => gate.waiting() === 1, '보내기가 재시도 대기에 들어감');
  assert.equal(s.status().state, 'sending', '화면이 기다리지 않아도 보내기가 진행 중');
  gate.release();
  await m.syncNow();
  assert.equal(s.status().state, 'processing');
});

test('결과 화면을 떠나면(signal) 기다리기와 4초 상태 확인만 멈추고, 보내기는 계속해 done까지 간다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const gate = gatedSleep();
  const m = makeModule(store, server, { sleep: gate.sleep });
  const s = await measured(m, store);
  server.fail('POST', /\/upload-urls$/, 1, { kind: 'network' });
  await s.complete();
  const leave = new AbortController();
  const waiting = s.waitForResult({ intervalMs: 4000, signal: leave.signal });
  await until(() => gate.waiting() === 1, '보내기가 재시도 대기');
  leave.abort();
  const p = await waiting;
  assert.equal(p.state, 'sending', '떠날 때 상태를 돌려주고 끝남');
  assert.equal(server.count('GET', /\/v1\/sessions\//), 0, '떠난 뒤 상태 확인을 하지 않음');
  // 화면을 떠나도 보내기는 이어진다
  gate.release();
  await m.syncNow();
  assert.equal(s.status().state, 'processing');
  // 앱이 앞으로 오거나 메인에서 상태만 갱신하면 결과가 들어온다
  server.finish(s.id, 'done');
  await m.refreshStatuses();
  assert.equal(s.status().state, 'done');
  assert.equal(store.files.size, 0, '202 뒤 기기 복사 파일 삭제');
});

test('상태 확인 중(processing)에 떠나도 멈추고, 다시 들어와 기다리면 결과를 받는다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const gate = gatedSleep();
  const m = makeModule(store, server, { sleep: gate.sleep });
  const s = await measured(m, store);
  await s.complete();
  const leave = new AbortController();
  const waiting = s.waitForResult({ intervalMs: 4000, signal: leave.signal });
  await until(() => gate.waiting() === 1, '4초 상태 확인 대기');
  assert.equal(s.status().state, 'processing');
  const gets = server.count('GET', /\/v1\/sessions\//);
  leave.abort();
  assert.equal((await waiting).state, 'processing');
  gate.release();
  await tick();
  assert.equal(server.count('GET', /\/v1\/sessions\//), gets, '멈춘 뒤 더 묻지 않음');
  server.finish(s.id, 'done');
  const again = s.waitForResult({ intervalMs: 4000 });
  assert.equal((await again).state, 'done');
});

test('앱이 앞으로 오면(syncNow) 못 보낸 회차를 이어 보내고, 메인 갱신(refreshStatuses)은 보내기를 새로 시작하지 않는다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  const m = makeModule(store, server);
  const s = await measured(m, store);
  server.fail('POST', /\/upload-urls$/, 6, { kind: 'network' });
  await s.complete();
  await m.syncNow();
  assert.equal(s.status().state, 'queued');
  const [summary] = await m.listSessions();
  assert.deepEqual([summary.state, summary.lastErrorCode, summary.uploaded, summary.total], ['queued', 'NETWORK', 0, 4]);
  const before = server.log.length;
  await m.refreshStatuses();
  assert.equal(server.log.length, before, '메인 갱신은 보내지 않음');
  assert.equal(s.status().state, 'queued');
  await m.syncNow();
  assert.equal(s.status().state, 'processing');
  server.finish(s.id, 'done');
  await m.syncNow();
  assert.equal(s.status().state, 'done');
  const [after] = await m.listSessions();
  assert.deepEqual([after.state, after.serverStatus, after.lastErrorCode, after.uploaded], ['done', 'done', null, 4]);
});

test('업로드 도중 앱이 꺼진 회차(강제 종료)가 다음 실행에서 남은 녹음만 올리고 done까지 간다', async () => {
  const store = new MemoryStore();
  const server = new FakeServer();
  // 첫 실행: 세 번째 S3 업로드에서 응답이 끊기고, 앱은 그 자리에서 꺼진다(재시도 대기가 끝나지 않음)
  server.fail('PUT', S3_PUT, 1, { kind: 'hang' }, 2);
  const first = makeModule(store, server, { sleep: () => new Promise<void>(() => {}), uploadTimeoutMs: 50 });
  const s = await measured(first, store);
  await s.complete();
  await until(async () => {
    const doc = (await store.readJson(`sessions/${s.id}.json`)) as { state: string; recordings: { uploaded: boolean }[] };
    return doc.state === 'sending' && doc.recordings.filter((r) => r.uploaded).length === 2 && server.count('PUT', S3_PUT) === 3;
  }, '두 개 올리고 세 번째에서 멈춘 채 저장');
  assert.equal(server.sessions.get(s.id)?.complete, null, 'complete 전');

  // 다시 실행: 디스크(저장소)에 남은 것만으로 이어서 보낸다
  const next = makeModule(store, server);
  const { resumed, unfinished } = await next.resumePending();
  assert.equal(resumed, 1, 'sending으로 남은 회차를 queued로 되돌려 이어 보냄');
  assert.equal(unfinished.length, 0);
  await next.syncNow();
  const reopened = (await next.openSession(s.id)) as Session;
  assert.equal(reopened.status().state, 'processing');
  // 이미 올린 두 개는 다시 올리지 않고, 남은 두 개만 올린다
  const fs = server.sessions.get(s.id);
  const putsPer = [...(fs?.recordings.keys() ?? [])].map((rid) => server.log.filter((x) => x.method === 'PUT' && x.url.includes(rid)).length);
  assert.deepEqual(putsPer, [1, 1, 2, 1], '세 번째는 끊긴 시도 + 이어 보내기');
  assert.equal(fs?.completeCalls, 1);
  assert.equal(server.sessions.size, 1, '같은 세션 ID로 한 번만 등록');
  assert.equal(server.users.size, 1);
  server.finish(s.id, 'done');
  await next.refreshStatuses();
  assert.equal(reopened.status().state, 'done');
  assert.equal(store.files.size, 0);
});
