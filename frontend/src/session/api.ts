// 서버 API 호출(docs/api.md 4장)과 오류 분류. fetch는 주입한다(앱: 전역 fetch, 테스트: 가짜 서버).

import type { DeviceMeasurement, GameRun, History, Purpose, RecordingKind, ServerStatus, SessionDetail } from './types';

export interface FetchInit {
  method: string;
  headers?: Record<string, string>;
  body?: string | Uint8Array;
  signal?: AbortSignal;
}
export interface FetchResponseLike {
  status: number;
  text(): Promise<string>;
}
export type FetchLike = (url: string, init: FetchInit) => Promise<FetchResponseLike>;

export interface SessionIn {
  user_id: string;
  purpose: Purpose;
  started_at: string;
  device_model: string;
  os_version: string;
  app_version: string;
  input_device: string;
  sample_rate: number;
  client_meta?: object;
}

export interface ServerSession {
  id: string;
  status: ServerStatus;
  is_baseline: boolean;
}

export interface UploadRequest {
  recording_id: string;
  kind: RecordingKind;
  trial_no?: number;
  sentence_id?: string;
  game_run_id?: string;
  agc_signal?: string;
  duration_s: number;
  sample_rate: number;
  size_bytes: number;
}

export interface UploadTarget {
  recording_id: string;
  url: string;
  method: string;
  headers: Record<string, string>;
  expires_at: string;
}

export interface CompleteIn {
  ended_at: string;
  device_measurements: DeviceMeasurement[];
  game_runs: GameRun[];
  client_meta?: object;
}

/** 서버가 오류 형식({"error": {...}})으로 답한 경우 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;
  constructor(status: number, code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** S3 업로드가 2xx가 아닌 경우. code는 S3 XML의 <Code> */
export class UploadError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** 연결 실패, 시간 초과, 응답이 JSON이 아님 */
export class NetworkError extends Error {}

/**
 * 오류를 재시도 규칙(src/session/README.md)으로 나눈다
 * - retry: 잠시 뒤 같은 요청을 다시 보냄(연결 실패, 5xx, S3 5xx)
 * - user_missing / session_missing: 등록부터 다시
 * - missing_recordings: 빠진 녹음만 다시 올리고 complete를 다시 보냄
 * - upload_expired: 업로드 URL을 다시 받음(S3 403)
 * - invalid_recordings: 녹음 규격 오류(422 INVALID_RECORDING_FORMAT). 기기에서 고칠 수 없음
 * - blocked: 다시 보내도 같음(401, 409 ID 충돌, 413, 422, 404·405 경로 오류, 그 밖의 4xx)
 */
export type ErrorKind =
  | 'retry'
  | 'user_missing'
  | 'session_missing'
  | 'missing_recordings'
  | 'upload_expired'
  | 'invalid_recordings'
  | 'blocked';

export function classifyError(error: unknown): ErrorKind {
  if (error instanceof NetworkError) return 'retry';
  if (error instanceof UploadError) {
    if (error.status >= 500 || error.status === 429) return 'retry';
    if (error.status === 403 || error.status === 400) return 'upload_expired';
    return 'blocked';
  }
  if (error instanceof ApiError) {
    if (error.status >= 500 || error.status === 429) return 'retry';
    if (error.code === 'USER_NOT_FOUND') return 'user_missing';
    if (error.code === 'SESSION_NOT_FOUND') return 'session_missing';
    if (error.code === 'MISSING_RECORDINGS') return 'missing_recordings';
    if (error.code === 'INVALID_RECORDING_FORMAT') return 'invalid_recordings';
    return 'blocked';
  }
  return 'blocked';
}

export interface ApiClientOptions {
  baseUrl: string;
  appKey: string;
  fetch: FetchLike;
  /** JSON 요청 시간 한도(밀리초) */
  timeoutMs?: number;
  /** S3 업로드 시간 한도(밀리초) */
  uploadTimeoutMs?: number;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly appKey: string;
  private readonly fetchFn: FetchLike;
  private readonly timeoutMs: number;
  private readonly uploadTimeoutMs: number;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '');
    this.appKey = options.appKey;
    this.fetchFn = options.fetch;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.uploadTimeoutMs = options.uploadTimeoutMs ?? 120_000;
  }

  putUser(userId: string, createdAt: string): Promise<{ id: string; created_at: string }> {
    return this.json('PUT', `/v1/users/${userId}`, { created_at: createdAt });
  }

  putSession(sessionId: string, body: SessionIn): Promise<ServerSession> {
    return this.json('PUT', `/v1/sessions/${sessionId}`, body);
  }

  async uploadUrls(sessionId: string, recordings: UploadRequest[]): Promise<UploadTarget[]> {
    const out = await this.json<{ uploads: UploadTarget[] }>('POST', `/v1/sessions/${sessionId}/upload-urls`, {
      recordings,
    });
    return out.uploads;
  }

  complete(sessionId: string, body: CompleteIn): Promise<{ session_id: string; status: ServerStatus }> {
    return this.json('POST', `/v1/sessions/${sessionId}/complete`, body);
  }

  getSession(sessionId: string): Promise<SessionDetail> {
    return this.json('GET', `/v1/sessions/${sessionId}`);
  }

  getHistory(userId: string): Promise<History> {
    return this.json('GET', `/v1/users/${userId}/history`);
  }

  /** 받은 업로드 URL로 WAV 바이트를 그대로 올린다. headers는 받은 그대로 붙인다(api.md 4.3) */
  async upload(target: UploadTarget, bytes: Uint8Array): Promise<void> {
    const res = await this.send(target.url, { method: target.method, headers: { ...target.headers }, body: bytes }, this.uploadTimeoutMs);
    if (res.status >= 200 && res.status < 300) return;
    const text = await safeText(res);
    const code = /<Code>([^<]+)<\/Code>/.exec(text)?.[1] ?? `HTTP_${res.status}`;
    const message = /<Message>([^<]+)<\/Message>/.exec(text)?.[1] ?? text.slice(0, 200);
    throw new UploadError(res.status, code, message);
  }

  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { 'X-App-Key': this.appKey, Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const res = await this.send(
      this.baseUrl + path,
      { method, headers, body: body === undefined ? undefined : JSON.stringify(body) },
      this.timeoutMs,
    );
    const text = await safeText(res);
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        if (res.status >= 500) throw new ApiError(res.status, 'INTERNAL_ERROR', text.slice(0, 200));
        throw new NetworkError(`JSON이 아닌 응답(${res.status}): ${text.slice(0, 120)}`);
      }
    }
    if (res.status >= 200 && res.status < 300) return parsed as T;
    const err = (parsed as { error?: Record<string, unknown> } | null)?.error;
    if (err && typeof err.code === 'string') {
      throw new ApiError(res.status, err.code, String(err.message ?? ''), err);
    }
    throw new ApiError(res.status, `HTTP_${res.status}`, text.slice(0, 200));
  }

  private async send(url: string, init: FetchInit, timeoutMs: number): Promise<FetchResponseLike> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await this.fetchFn(url, { ...init, signal: controller.signal });
    } catch (e) {
      throw new NetworkError(e instanceof Error ? e.message : String(e));
    } finally {
      clearTimeout(timer);
    }
  }
}

async function safeText(res: FetchResponseLike): Promise<string> {
  try {
    return await res.text();
  } catch {
    return '';
  }
}
