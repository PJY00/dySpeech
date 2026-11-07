import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiClient, ApiError, classifyError, NetworkError, UploadError } from '../api';
import type { FetchLike } from '../api';

test('오류 분류: 재시도 규칙 표와 같다', () => {
  assert.equal(classifyError(new NetworkError('x')), 'retry');
  assert.equal(classifyError(new ApiError(500, 'INTERNAL_ERROR', '')), 'retry');
  assert.equal(classifyError(new ApiError(503, 'HTTP_503', '')), 'retry');
  assert.equal(classifyError(new ApiError(404, 'USER_NOT_FOUND', '')), 'user_missing');
  assert.equal(classifyError(new ApiError(404, 'SESSION_NOT_FOUND', '')), 'session_missing');
  assert.equal(classifyError(new ApiError(409, 'MISSING_RECORDINGS', '')), 'missing_recordings');
  assert.equal(classifyError(new ApiError(422, 'INVALID_RECORDING_FORMAT', '')), 'invalid_recordings');
  for (const [status, code] of [
    [401, 'INVALID_APP_KEY'],
    [409, 'ID_CONFLICT'],
    [413, 'PAYLOAD_TOO_LARGE'],
    [422, 'VALIDATION_ERROR'],
    [404, 'NOT_FOUND'],
    [405, 'METHOD_NOT_ALLOWED'],
  ] as const) {
    assert.equal(classifyError(new ApiError(status, code, '')), 'blocked', code);
  }
  assert.equal(classifyError(new UploadError(403, 'AccessDenied', 'Request has expired')), 'upload_expired');
  assert.equal(classifyError(new UploadError(503, 'SlowDown', '')), 'retry');
  assert.equal(classifyError(new Error('?')), 'blocked');
});

test('오류 응답의 code·message·추가 필드를 읽고, 앱 키를 헤더에 넣는다', async () => {
  const seen: Record<string, string>[] = [];
  const fetch: FetchLike = async (_url, init) => {
    seen.push(init.headers ?? {});
    return { status: 409, text: async () => JSON.stringify({ error: { code: 'MISSING_RECORDINGS', message: '없음', missing_recording_ids: ['a'] } }) };
  };
  const api = new ApiClient({ baseUrl: 'http://x/', appKey: 'k', fetch });
  await assert.rejects(api.complete('s', { ended_at: 't', device_measurements: [], game_runs: [] }), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 409);
    assert.equal(e.code, 'MISSING_RECORDINGS');
    assert.deepEqual(e.detail.missing_recording_ids, ['a']);
    return true;
  });
  assert.equal(seen[0]['X-App-Key'], 'k');
  assert.equal(seen[0]['Content-Type'], 'application/json');
});

test('S3 오류 XML에서 Code와 Message를 꺼내고, 연결 실패는 NetworkError', async () => {
  const s3: FetchLike = async () => ({
    status: 403,
    text: async () => '<?xml version="1.0"?><Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>',
  });
  const api = new ApiClient({ baseUrl: 'http://x', appKey: 'k', fetch: s3 });
  const target = { recording_id: 'r', url: 'https://s3/x', method: 'PUT', headers: { 'Content-Type': 'audio/wav' }, expires_at: '' };
  await assert.rejects(api.upload(target, new Uint8Array(4)), (e: unknown) => {
    assert.ok(e instanceof UploadError);
    assert.equal(e.code, 'AccessDenied');
    assert.equal(e.message, 'Request has expired');
    return true;
  });
  const down = new ApiClient({ baseUrl: 'http://x', appKey: 'k', fetch: async () => { throw new TypeError('Network request failed'); } });
  await assert.rejects(down.getSession('s'), NetworkError);
});
