// 보내기 작업: 사용자 등록 → 세션 등록 → 업로드 URL → S3 PUT → complete → 상태 확인(docs/api.md 6장).
// 한 번에 하나만 돈다. 같은 ID로 다시 보내도 서버 결과가 같으므로(api.md 2.6) 끊기면 처음부터 다시 한다.

import { ApiError, classifyError, NetworkError, UploadError } from './api';
import type { ApiClient, UploadRequest, UploadTarget } from './api';
import type { Repo, SessionRecord } from './records';
import type { Store } from './store';
import type { ErrorInfo } from './types';

export type Logger = (event: string, data?: Record<string, unknown>) => void;

export interface SyncDeps {
  api: ApiClient;
  apiConfigured: boolean;
  store: Store;
  repo: Repo;
  now: () => Date;
  sleep: (ms: number) => Promise<void>;
  /** 다시 보내기 전 기다리는 시간(밀리초). 길이가 최대 재시도 횟수 */
  retryDelaysMs: number[];
  log: Logger;
}

/** 등록·업로드를 처음부터 다시 하는 최대 횟수(404 등록 없음, 409 빠진 녹음, URL 만료) */
const MAX_RESTARTS = 3;

export class Syncer {
  private readonly deps: SyncDeps;
  private running: Promise<void> | null = null;
  private again = false;

  constructor(deps: SyncDeps) {
    this.deps = deps;
  }

  /**
   * 보낼 세션(queued·sending)을 오래된 것부터 보낸다. 이미 돌고 있으면 끝난 뒤 한 번 더 돈다
   * 한 차례 안에서 이미 시도한 세션은 다시 보내지 않는다(실패한 세션은 다음 계기에)
   */
  kick(): Promise<void> {
    if (this.running) {
      this.again = true;
      return this.running;
    }
    const attempted = new Set<string>();
    this.running = (async () => {
      try {
        do {
          this.again = false;
          await this.runOnce(attempted);
        } while (this.again);
      } finally {
        this.running = null;
      }
    })();
    return this.running;
  }

  /** 서버 처리 상태를 한 번 확인해 기기 상태에 반영한다. 연결 오류는 기록만 하고 넘긴다 */
  async refresh(id: string): Promise<SessionRecord | null> {
    const { api, repo, now, log } = this.deps;
    const rec = await repo.loadSession(id);
    if (!rec || !rec.completeAccepted || rec.state === 'done' || rec.state === 'failed') return rec;
    try {
      const detail = await api.getSession(id);
      rec.server = detail;
      if (detail.status === 'done' || detail.status === 'failed') rec.state = detail.status;
      else if (detail.status === 'processing') rec.state = 'processing';
      await repo.saveSession(rec, now());
      log('status', { session_id: id, status: detail.status });
    } catch (e) {
      rec.lastError = errorInfo(e, now());
      await repo.saveSession(rec, now());
      log('status_error', { session_id: id, ...rec.lastError });
    }
    return rec;
  }

  private async runOnce(attempted: Set<string>): Promise<void> {
    const { repo } = this.deps;
    const records: SessionRecord[] = [];
    for (const id of await repo.listSessionIds()) {
      if (attempted.has(id)) continue;
      const rec = await repo.loadSession(id);
      if (rec && (rec.state === 'queued' || rec.state === 'sending')) records.push(rec);
    }
    records.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
    for (const rec of records) {
      attempted.add(rec.id);
      await this.sendOne(rec);
    }
  }

  private async sendOne(rec: SessionRecord): Promise<void> {
    const { repo, now, log, apiConfigured } = this.deps;
    if (!apiConfigured) {
      rec.state = 'queued';
      rec.lastError = { code: 'CONFIG_MISSING', message: 'EXPO_PUBLIC_API_URL 또는 EXPO_PUBLIC_APP_KEY가 비어 있습니다', at: now().toISOString() };
      await repo.saveSession(rec, now());
      log('send_skipped', { session_id: rec.id, reason: 'CONFIG_MISSING' });
      return;
    }
    rec.state = 'sending';
    rec.sendAttempts += 1;
    await repo.saveSession(rec, now());
    log('send_start', { session_id: rec.id, attempt: rec.sendAttempts, recordings: rec.recordings.length });
    try {
      await this.sendSteps(rec);
      rec.lastError = null;
      await repo.saveSession(rec, now());
      log('send_done', { session_id: rec.id, state: rec.state });
    } catch (e) {
      const kind = classifyError(e);
      rec.lastError = errorInfo(e, now());
      rec.state = kind === 'retry' ? 'queued' : 'blocked';
      await repo.saveSession(rec, now());
      log('send_failed', { session_id: rec.id, kind, state: rec.state, ...rec.lastError });
    }
  }

  private async sendSteps(rec: SessionRecord): Promise<void> {
    const { api, repo, now } = this.deps;
    let restarts = 0;
    for (;;) {
      try {
        await this.ensureUser(rec.userId);
        if (!rec.sessionRegistered) {
          const server = await this.withRetry('put_session', () =>
            api.putSession(rec.id, {
              user_id: rec.userId,
              purpose: rec.purpose,
              started_at: rec.startedAt,
              device_model: rec.device.device_model,
              os_version: rec.device.os_version,
              app_version: rec.device.app_version,
              input_device: rec.inputDevice,
              sample_rate: rec.sampleRate,
              client_meta: rec.clientMeta,
            }),
          );
          rec.sessionRegistered = true;
          await repo.saveSession(rec, now());
          if (server.status !== 'open') {
            // 앞선 실행에서 complete가 이미 받아들여졌다.
            await this.accepted(rec);
            return;
          }
        }
        await this.uploadPending(rec);
        await this.withRetry('complete', () =>
          api.complete(rec.id, {
            ended_at: rec.endedAt ?? now().toISOString(),
            device_measurements: rec.deviceMeasurements,
            game_runs: rec.gameRuns,
            client_meta: rec.completeMeta,
          }),
        );
        await this.accepted(rec);
        return;
      } catch (e) {
        const kind = classifyError(e);
        if (restarts >= MAX_RESTARTS) throw e;
        if (kind === 'user_missing') {
          const user = await repo.loadUser(rec.userId);
          if (user) {
            user.registered = false;
            await repo.saveUser(user);
          }
          rec.sessionRegistered = false;
        } else if (kind === 'session_missing') {
          rec.sessionRegistered = false;
        } else if (kind === 'missing_recordings' && e instanceof ApiError) {
          const missing = new Set((e.detail.missing_recording_ids as string[] | undefined) ?? []);
          for (const r of rec.recordings) if (missing.has(r.recordingId)) r.uploaded = false;
        } else {
          throw e;
        }
        restarts += 1;
        await repo.saveSession(rec, now());
        this.deps.log('restart', { session_id: rec.id, kind, restarts });
      }
    }
  }

  private async accepted(rec: SessionRecord): Promise<void> {
    const { repo, store, now, log } = this.deps;
    rec.completeAccepted = true;
    rec.state = 'processing';
    for (const r of rec.recordings) r.uploaded = true;
    await repo.saveSession(rec, now());
    // 서버가 모든 녹음이 S3에 있음을 확인했으므로 기기의 복사 파일을 지운다.
    for (const r of rec.recordings) {
      try {
        await store.deleteFile(r.fileUri);
      } catch (e) {
        log('delete_failed', { recording_id: r.recordingId, message: String(e) });
      }
    }
  }

  private async ensureUser(userId: string): Promise<void> {
    const { api, repo } = this.deps;
    const user = await repo.loadUser(userId);
    if (!user) throw new Error(`기기에 사용자 문서가 없습니다: ${userId}`);
    if (user.registered) return;
    await this.withRetry('put_user', () => api.putUser(user.userId, user.createdAt));
    user.registered = true;
    await repo.saveUser(user);
  }

  private async uploadPending(rec: SessionRecord): Promise<void> {
    const { api, store, repo, now, log } = this.deps;
    let expired = 0;
    for (;;) {
      const pending = rec.recordings.filter((r) => !r.uploaded);
      if (pending.length === 0) return;
      const targets = await this.withRetry('upload_urls', () => api.uploadUrls(rec.id, pending.map(uploadRequest)));
      const byId = new Map<string, UploadTarget>(targets.map((t) => [t.recording_id, t]));
      let renew = false;
      for (const r of pending) {
        const target = byId.get(r.recordingId);
        if (!target) throw new Error(`업로드 URL 응답에 녹음이 없습니다: ${r.recordingId}`);
        const bytes = await store.readFile(r.fileUri);
        try {
          await this.withRetry('upload', () => api.upload(target, bytes));
        } catch (e) {
          if (classifyError(e) === 'upload_expired' && expired < MAX_RESTARTS) {
            expired += 1;
            renew = true;
            log('upload_renew', { recording_id: r.recordingId, code: e instanceof UploadError ? e.code : '' });
            break;
          }
          throw e;
        }
        r.uploaded = true;
        await repo.saveSession(rec, now());
        log('uploaded', { recording_id: r.recordingId, kind: r.kind, bytes: bytes.length });
      }
      if (!renew) return;
    }
  }

  private async withRetry<T>(step: string, job: () => Promise<T>): Promise<T> {
    const { retryDelaysMs, sleep, log } = this.deps;
    for (let attempt = 0; ; attempt++) {
      try {
        return await job();
      } catch (e) {
        if (classifyError(e) !== 'retry' || attempt >= retryDelaysMs.length) throw e;
        log('retry', { step, attempt: attempt + 1, wait_ms: retryDelaysMs[attempt], message: e instanceof Error ? e.message : String(e) });
        await sleep(retryDelaysMs[attempt]);
      }
    }
  }
}

function uploadRequest(r: SessionRecord['recordings'][number]): UploadRequest {
  const req: UploadRequest = {
    recording_id: r.recordingId,
    kind: r.kind,
    duration_s: Math.round(r.durationS * 1000) / 1000,
    sample_rate: r.sampleRate,
    size_bytes: r.sizeBytes,
  };
  if (r.trialNo !== undefined) req.trial_no = r.trialNo;
  if (r.sentenceId !== undefined) req.sentence_id = r.sentenceId;
  if (r.gameRunId !== undefined) req.game_run_id = r.gameRunId;
  if (r.agcSignal !== undefined) req.agc_signal = r.agcSignal;
  return req;
}

export function errorInfo(e: unknown, now: Date): ErrorInfo {
  const at = now.toISOString();
  if (e instanceof ApiError) {
    // 422의 fields, invalid_recordings는 무엇을 고칠지 알려 주므로 함께 남긴다.
    const extra = e.detail.fields ?? e.detail.invalid_recordings;
    return { code: e.code, message: extra ? `${e.message} ${JSON.stringify(extra)}` : e.message, at };
  }
  if (e instanceof UploadError) return { code: `S3_${e.code}`, message: e.message, at };
  if (e instanceof NetworkError) return { code: 'NETWORK', message: e.message, at };
  return { code: 'APP_ERROR', message: e instanceof Error ? e.message : String(e), at };
}
