// 테스트용 가짜 서버. docs/api.md의 동작(등록, 업로드 URL, S3 PUT, complete 검사, 상태)을 메모리로 흉내 낸다.
// fail()로 오류를 끼워 넣어 재시도 규칙을 시험한다.

import type { FetchInit, FetchLike, FetchResponseLike } from '../api';
import { parseWav, wavProblems } from '../wav';

export const FAKE_KEY = 'test-key';
const S3 = 'https://s3.fake/';

type Fault =
  | { kind: 'network' }
  | { kind: 'status'; status: number; body: unknown }
  | { kind: 's3'; status: number; code: string };

interface FaultRule {
  method: string;
  pattern: RegExp;
  times: number;
  fault: Fault;
}

export interface FakeSession {
  id: string;
  body: Record<string, unknown>;
  status: 'open' | 'processing' | 'done' | 'failed';
  recordings: Map<string, Record<string, unknown>>;
  complete: Record<string, unknown> | null;
  completeCalls: number;
}

export interface RequestLog {
  method: string;
  url: string;
  body: unknown;
}

function response(status: number, body: unknown): FetchResponseLike {
  const text = body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body);
  return { status, text: async () => text };
}

const err = (status: number, code: string, message: string, extra: Record<string, unknown> = {}) =>
  response(status, { error: { code, message, ...extra } });

export class FakeServer {
  readonly users = new Map<string, string>();
  readonly sessions = new Map<string, FakeSession>();
  readonly objects = new Map<string, Uint8Array>();
  readonly log: RequestLog[] = [];
  /** 다음 S3 PUT 몇 개는 200을 주되 저장하지 않는다(올라가지 않은 녹음 흉내). 그 녹음 ID는 dropped에 남는다 */
  dropNextUploads = 0;
  readonly dropped: string[] = [];
  private readonly rules: FaultRule[] = [];
  private signature = 0;

  /** method와 경로가 맞는 다음 요청 times개에 fault를 낸다 */
  fail(method: string, pattern: RegExp, times: number, fault: Fault): void {
    this.rules.push({ method, pattern, times, fault });
  }

  /** 서버 계산이 끝난 것처럼 상태를 바꾼다 */
  finish(sessionId: string, status: 'done' | 'failed'): void {
    const s = this.sessions.get(sessionId);
    if (s) s.status = status;
  }

  count(method: string, pattern: RegExp): number {
    return this.log.filter((r) => r.method === method && pattern.test(r.url)).length;
  }

  readonly fetch: FetchLike = async (url: string, init: FetchInit) => {
    const method = init.method.toUpperCase();
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
    this.log.push({ method, url, body: body instanceof Uint8Array ? `<${body.length} bytes>` : body });
    const rule = this.rules.find((r) => r.times > 0 && r.method === method && r.pattern.test(url));
    if (rule) {
      rule.times -= 1;
      const f = rule.fault;
      if (f.kind === 'network') throw new TypeError('Network request failed');
      if (f.kind === 'status') return response(f.status, f.body);
      return response(f.status, `<?xml version="1.0"?><Error><Code>${f.code}</Code><Message>fake ${f.code}</Message></Error>`);
    }
    if (url.startsWith(S3)) return this.s3(method, url, init);
    if (init.headers?.['X-App-Key'] !== FAKE_KEY) return err(401, 'INVALID_APP_KEY', 'X-App-Key가 없거나 맞지 않습니다');
    const path = new URL(url).pathname;
    let m: RegExpExecArray | null;
    if (method === 'PUT' && (m = /^\/v1\/users\/([^/]+)$/.exec(path))) return this.putUser(m[1], body as Record<string, unknown>);
    if (method === 'PUT' && (m = /^\/v1\/sessions\/([^/]+)$/.exec(path))) return this.putSession(m[1], body as Record<string, unknown>);
    if (method === 'POST' && (m = /^\/v1\/sessions\/([^/]+)\/upload-urls$/.exec(path))) return this.uploadUrls(m[1], body as { recordings: Record<string, unknown>[] });
    if (method === 'POST' && (m = /^\/v1\/sessions\/([^/]+)\/complete$/.exec(path))) return this.complete(m[1], body as Record<string, unknown>);
    if (method === 'GET' && (m = /^\/v1\/sessions\/([^/]+)$/.exec(path))) return this.getSession(m[1]);
    if (method === 'GET' && (m = /^\/v1\/users\/([^/]+)\/history$/.exec(path))) {
      return this.users.has(m[1]) ? response(200, { user_id: m[1], metrics: [], recent_device_measurements: [] }) : err(404, 'USER_NOT_FOUND', '사용자 없음');
    }
    return err(404, 'NOT_FOUND', '없는 경로');
  };

  private putUser(id: string, body: Record<string, unknown>): FetchResponseLike {
    const createdAt = String(body.created_at);
    const existing = this.users.get(id);
    if (existing !== undefined && existing !== createdAt) return err(409, 'ID_CONFLICT', '같은 user_id에 다른 created_at');
    this.users.set(id, createdAt);
    return response(200, { id, created_at: createdAt });
  }

  private putSession(id: string, body: Record<string, unknown>): FetchResponseLike {
    if (!this.users.has(String(body.user_id))) return err(404, 'USER_NOT_FOUND', '사용자 없음');
    const existing = this.sessions.get(id);
    if (!existing) {
      this.sessions.set(id, { id, body, status: 'open', recordings: new Map(), complete: null, completeCalls: 0 });
    }
    const s = this.sessions.get(id) as FakeSession;
    return response(200, { id, status: s.status, is_baseline: false });
  }

  private uploadUrls(id: string, body: { recordings: Record<string, unknown>[] }): FetchResponseLike {
    const s = this.sessions.get(id);
    if (!s) return err(404, 'SESSION_NOT_FOUND', '세션 없음');
    const uploads = body.recordings.map((r) => {
      const rid = String(r.recording_id);
      s.recordings.set(rid, r);
      this.signature += 1;
      return {
        recording_id: rid,
        url: `${S3}${id}/${rid}.wav?sig=${this.signature}`,
        method: 'PUT',
        headers: { 'Content-Type': 'audio/wav' },
        expires_at: '2099-01-01T00:00:00Z',
      };
    });
    return response(200, { uploads });
  }

  private s3(method: string, url: string, init: FetchInit): FetchResponseLike {
    if (method !== 'PUT') return response(405, '');
    if (init.headers?.['Content-Type'] !== 'audio/wav') return response(403, '<Error><Code>SignatureDoesNotMatch</Code></Error>');
    const key = new URL(url).pathname.slice(1);
    const rid = key.split('/')[1].replace('.wav', '');
    if (this.dropNextUploads > 0) {
      this.dropNextUploads -= 1;
      this.dropped.push(rid);
    } else {
      this.objects.set(key, init.body as Uint8Array);
    }
    return response(200, '');
  }

  private complete(id: string, body: Record<string, unknown>): FetchResponseLike {
    const s = this.sessions.get(id);
    if (!s) return err(404, 'SESSION_NOT_FOUND', '세션 없음');
    s.completeCalls += 1;
    if (s.status !== 'open') return response(202, { session_id: id, status: s.status });
    const missing = [...s.recordings.keys()].filter((rid) => !this.objects.has(`${id}/${rid}.wav`));
    if (missing.length) return err(409, 'MISSING_RECORDINGS', '올라오지 않은 녹음', { missing_recording_ids: missing });
    const invalid: { recording_id: string; reason: string }[] = [];
    for (const rid of s.recordings.keys()) {
      const problems = wavProblems(parseWav(this.objects.get(`${id}/${rid}.wav`) as Uint8Array));
      if (problems.length) invalid.push({ recording_id: rid, reason: problems.join(' / ') });
    }
    if (invalid.length) return err(422, 'INVALID_RECORDING_FORMAT', '녹음 파일이 규격과 다릅니다', { invalid_recordings: invalid });
    s.complete = body;
    s.status = 'processing';
    return response(202, { session_id: id, status: 'processing' });
  }

  private getSession(id: string): FetchResponseLike {
    const s = this.sessions.get(id);
    if (!s) return err(404, 'SESSION_NOT_FOUND', '세션 없음');
    return response(200, {
      ...s.body,
      id,
      ended_at: s.complete?.ended_at ?? null,
      status: s.status,
      is_baseline: false,
      server_measurements: s.status === 'done' ? [{ metric: 'noise_floor', value: -60, unit: 'dBFS', low_reliability: false, algorithm_version: '1.0.0' }] : [],
      error: s.status === 'failed' ? { code: 'PROCESSING_FAILED', message: '계산 오류' } : null,
    });
  }
}
