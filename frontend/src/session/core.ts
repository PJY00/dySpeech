// 세션 모듈의 기능. 저장소·API·시계를 주입받아 앱(index.ts)과 테스트가 같은 코드를 쓴다.

import type { ApiClient } from './api';
import { ApiError } from './api';
import { Repo } from './records';
import type { DeviceInfo, RecordingRecord, SessionRecord, UserRecord } from './records';
import { normalizeFileUri } from './store';
import type { Store } from './store';
import { errorInfo, Syncer } from './sync';
import type { Logger } from './sync';
import { METRIC_UNITS } from './types';
import type {
  Baselines,
  DeviceMeasurement,
  GameRun,
  History,
  HzRange,
  LocalState,
  Purpose,
  RecordingInput,
  SessionProgress,
  SessionSummary,
  UserInfo,
} from './types';
import { isUuidV4, uuidv4 } from './uuid';
import { parseWav, wavProblems } from './wav';
import type { WavInfo } from './wav';

export const SAMPLE_RATE = 48000;
/** 다시 보내기 전 기다리는 시간(초 단위 2, 5, 15, 30, 60). 최대 5회 */
export const RETRY_DELAYS_MS = [2_000, 5_000, 15_000, 30_000, 60_000];
export const POLL_INTERVAL_MS = 4_000;
export const POLL_TIMEOUT_MS = 180_000;
/** 기기에 남겨 두는 끝난 세션 문서 수 */
const KEEP_FINISHED = 50;

export class RecordingRejected extends Error {
  readonly problems: string[];
  constructor(problems: string[]) {
    super(problems.join(' / '));
    this.problems = problems;
  }
}

export interface Session {
  readonly id: string;
  readonly userId: string;
  readonly purpose: Purpose;
  readonly startedAt: string;
  /** 이 사용자의 캘리브레이션이 아직 없으면 true(첫 회차). agc_test는 항상 false */
  readonly needsCalibration: boolean;
  /** 저장된 캘리브레이션(편한 음높이 범위, Hz). 없으면 null */
  calibration(): HzRange | null;
  /** WAV 헤더를 검사하고 녹음을 세션에 넣는다. 규격과 다르면 RecordingRejected. 녹음 ID를 돌려준다 */
  addRecording(input: RecordingInput): Promise<string>;
  /** complete 전에 넣은 녹음을 빼고 파일을 지운다(다시 하기) */
  removeRecording(recordingId: string): Promise<void>;
  addGameRun(run: GameRun): Promise<void>;
  setDeviceMeasurements(measurements: DeviceMeasurement[]): Promise<void>;
  /** 측정 블록 기준값을 저장한다. calibration을 주면 사용자 캘리브레이션으로도 저장한다 */
  setBaselines(baselines: Baselines, options?: { calibration?: HzRange }): Promise<void>;
  setInputDevice(inputDevice: string): Promise<void>;
  baselines(): Baselines | null;
  /** 끝난 시각을 고정해 저장하고 보내기를 시작한다. 오프라인이어도 바로 돌아온다 */
  complete(options?: { clientMeta?: Record<string, unknown> }): Promise<void>;
  status(): SessionProgress;
  /**
   * 보내기가 끝나기를 기다린 뒤 서버 처리 상태를 간격(기본 4초)마다 확인한다(기본 3분까지)
   * signal을 멈추면(abort) 기다리기와 상태 확인만 멈추고 지금 상태를 돌려준다. 보내기는 세션 모듈이 계속한다
   */
  waitForResult(options?: {
    intervalMs?: number;
    timeoutMs?: number;
    onProgress?: (p: SessionProgress) => void;
    signal?: AbortSignal;
  }): Promise<SessionProgress>;
  /** complete 전에만: 세션과 녹음 파일을 지운다 */
  discard(): Promise<void>;
}

export interface SessionModuleDeps {
  store: Store;
  api: ApiClient;
  /** API 주소와 앱 키가 있는지. 없으면 보내지 않고 기다린다 */
  apiConfigured: boolean;
  device: () => DeviceInfo;
  now?: () => Date;
  sleep?: (ms: number) => Promise<void>;
  log?: Logger;
  retryDelaysMs?: number[];
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  uuid?: () => string;
  /** 녹음 파일 폴더 URI. 주면 resumePending이 어느 세션에도 없는 WAV(녹음 도중 앱이 꺼져 남은 파일)를 지운다 */
  recordingsDir?: string;
}

export interface SessionModule {
  startSession(purpose: Purpose, options?: { clientMeta?: Record<string, unknown> }): Promise<Session>;
  /**
   * 앱 시작 때 한 번: 못 보낸 세션을 이어서 보내고, complete 전에 멈춘 세션을 돌려준다.
   * 어느 세션에도 없는 녹음 파일(녹음 도중 앱이 꺼져 남은 WAV)도 지운다. 녹음 중에는 부르지 않는다
   */
  resumePending(): Promise<{ resumed: number; unfinished: Session[]; sweptFiles: number }>;
  /** 보내기와 처리 중 세션 상태 확인을 지금 하고 끝날 때까지 기다린다(앱이 앞으로 올 때) */
  syncNow(): Promise<void>;
  /** 서버 계산 중(processing)인 세션의 상태만 서버에서 다시 읽는다. 보내기는 새로 시작하지 않는다(메인 화면 갱신용) */
  refreshStatuses(): Promise<void>;
  getLatestBaselines(): Promise<Baselines | null>;
  /** 진전도. 사용자가 아직 서버에 없으면 null */
  getHistory(): Promise<History | null>;
  currentUser(): Promise<UserInfo>;
  /** 개발용: 새 사용자로 시작한다. 그 사용자의 세션은 client_meta.dev = true */
  startNewDevUser(): Promise<UserInfo>;
  /** 개발용으로 만든 사용자 ID(정리 대상) */
  devUserIds(): Promise<string[]>;
  listSessions(): Promise<SessionSummary[]>;
  openSession(sessionId: string): Promise<Session | null>;
  readRecordingFile(uri: string): Promise<Uint8Array>;
  discardRecordingFile(uri: string): Promise<void>;
}

const defaultLog: Logger = (event, data) => console.log('[session]', event, data ? JSON.stringify(data) : '');

export function createSessionModule(deps: SessionModuleDeps): SessionModule {
  const now = deps.now ?? (() => new Date());
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const log = deps.log ?? defaultLog;
  const newId = deps.uuid ?? (() => uuidv4());
  const pollIntervalMs = deps.pollIntervalMs ?? POLL_INTERVAL_MS;
  const pollTimeoutMs = deps.pollTimeoutMs ?? POLL_TIMEOUT_MS;
  const repo = new Repo(deps.store);
  const syncer = new Syncer({
    api: deps.api,
    apiConfigured: deps.apiConfigured,
    store: deps.store,
    repo,
    now,
    sleep,
    retryDelaysMs: deps.retryDelaysMs ?? RETRY_DELAYS_MS,
    log,
  });

  async function createUser(dev: boolean): Promise<UserRecord> {
    const user: UserRecord = {
      userId: newId(),
      createdAt: now().toISOString(),
      dev,
      registered: false,
      calibration: null,
      latestBaselines: null,
    };
    await repo.saveUser(user);
    await repo.setCurrentUser(user.userId);
    if (dev) await repo.addDevUser(user.userId);
    log('user_created', { user_id: user.userId, dev });
    return user;
  }

  async function currentUserRecord(): Promise<UserRecord> {
    const id = await repo.currentUserId();
    const user = id ? await repo.loadUser(id) : null;
    return user ?? createUser(false);
  }

  async function refreshProcessing(): Promise<void> {
    for (const id of await repo.listSessionIds()) {
      const rec = await repo.loadSession(id);
      if (rec && rec.state === 'processing') await syncer.refresh(id);
    }
  }

  /** 어느 세션 문서에도 없는 .wav를 지운다. 지운 개수를 돌려준다 */
  async function sweepOrphans(): Promise<number> {
    if (!deps.recordingsDir) return 0;
    const referenced = new Set<string>();
    for (const id of await repo.listSessionIds()) {
      const rec = await repo.loadSession(id);
      for (const r of rec?.recordings ?? []) referenced.add(normalizeFileUri(r.fileUri));
    }
    let swept = 0;
    for (const uri of await deps.store.listFiles(deps.recordingsDir)) {
      if (!uri.toLowerCase().endsWith('.wav') || referenced.has(normalizeFileUri(uri))) continue;
      try {
        await deps.store.deleteFile(uri);
        swept += 1;
      } catch (e) {
        log('sweep_failed', { uri, message: String(e) });
      }
    }
    return swept;
  }

  async function prune(): Promise<void> {
    const finished: SessionRecord[] = [];
    for (const id of await repo.listSessionIds()) {
      const rec = await repo.loadSession(id);
      if (rec && (rec.state === 'done' || rec.state === 'failed')) finished.push(rec);
    }
    finished.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    for (const rec of finished.slice(KEEP_FINISHED)) await repo.removeSession(rec.id);
  }

  function progress(rec: SessionRecord): SessionProgress {
    return {
      sessionId: rec.id,
      purpose: rec.purpose,
      state: rec.state,
      uploaded: rec.recordings.filter((r) => r.uploaded).length,
      total: rec.recordings.length,
      lastError: rec.lastError,
      server: rec.server,
    };
  }

  function handle(rec: SessionRecord, user: UserRecord): Session {
    const save = () => repo.saveSession(rec, now());
    const requireOpen = (what: string) => {
      if (rec.state !== 'recording') throw new Error(`complete 뒤에는 ${what}할 수 없습니다(상태 ${rec.state})`);
    };
    return {
      id: rec.id,
      userId: rec.userId,
      purpose: rec.purpose,
      startedAt: rec.startedAt,
      needsCalibration: rec.purpose !== 'agc_test' && user.calibration === null,
      calibration: () => user.calibration?.f0RangeHz ?? null,

      async addRecording(input) {
        requireOpen('녹음을 추가');
        const bytes = await deps.store.readFile(input.fileUri);
        let info: WavInfo;
        try {
          info = parseWav(bytes);
        } catch (e) {
          throw new RecordingRejected([e instanceof Error ? e.message : String(e)]);
        }
        const problems = wavProblems(info);
        if (problems.length) throw new RecordingRejected(problems);
        const record: RecordingRecord = {
          recordingId: newId(),
          kind: input.kind,
          fileUri: input.fileUri,
          durationS: info.durationS,
          sampleRate: info.sampleRate,
          sizeBytes: info.sizeBytes,
          addedAt: now().toISOString(),
          uploaded: false,
        };
        if (input.kind === 'mpt_trial') record.trialNo = input.trialNo;
        if (input.kind === 'eval_sentence') record.sentenceId = input.sentenceId;
        if (input.kind === 'game') record.gameRunId = input.gameRunId;
        if (input.kind === 'agc_tone') record.agcSignal = input.agcSignal;
        rec.recordings.push(record);
        await save();
        log('recording_added', { session_id: rec.id, recording_id: record.recordingId, kind: record.kind, duration_s: info.durationS, bytes: info.sizeBytes });
        return record.recordingId;
      },

      async removeRecording(recordingId) {
        requireOpen('녹음을 빼기');
        const r = rec.recordings.find((x) => x.recordingId === recordingId);
        if (!r) return;
        rec.recordings = rec.recordings.filter((x) => x.recordingId !== recordingId);
        await save();
        await deps.store.deleteFile(r.fileUri);
      },

      async addGameRun(run) {
        requireOpen('게임 라운드를 추가');
        if (!isUuidV4(run.game_run_id)) throw new Error(`game_run_id가 UUID v4가 아닙니다: ${run.game_run_id}`);
        if (!(run.success_rate_pct >= 0 && run.success_rate_pct <= 100)) {
          throw new Error(`success_rate_pct는 0~100이어야 합니다: ${run.success_rate_pct}`);
        }
        rec.gameRuns = [...rec.gameRuns.filter((g) => g.game_run_id !== run.game_run_id), run];
        await save();
        log('game_run_added', { session_id: rec.id, game: run.game, round_index: run.round_index, decision: run.decision });
      },

      async setDeviceMeasurements(measurements) {
        requireOpen('기기 지표를 바꾸기');
        const byMetric = new Map<string, DeviceMeasurement>();
        for (const m of measurements) {
          if (!Number.isFinite(m.value)) continue;
          if (METRIC_UNITS[m.metric] !== m.unit) throw new Error(`${m.metric}의 단위는 ${METRIC_UNITS[m.metric]}이어야 합니다`);
          byMetric.set(m.metric, { metric: m.metric, value: m.value, unit: m.unit, low_reliability: m.low_reliability ?? false });
        }
        rec.deviceMeasurements = [...byMetric.values()];
        await save();
      },

      async setBaselines(baselines, options) {
        rec.baselines = baselines;
        user.latestBaselines = baselines;
        if (options?.calibration) {
          user.calibration = { f0RangeHz: options.calibration, sessionId: rec.id, measuredAt: baselines.measuredAt };
        }
        await save();
        await repo.saveUser(user);
      },

      async setInputDevice(inputDevice) {
        rec.inputDevice = inputDevice.trim().slice(0, 100) || 'unknown';
        await save();
      },

      baselines: () => rec.baselines,

      async complete(options) {
        if (rec.state !== 'recording') return;
        rec.endedAt = now().toISOString();
        rec.completeMeta = { ...(options?.clientMeta ?? {}), ...(user.dev ? { dev: true } : {}) };
        rec.state = 'queued';
        await save();
        log('session_completed', { session_id: rec.id, recordings: rec.recordings.length, game_runs: rec.gameRuns.length });
        void syncer.kick();
      },

      status: () => progress(rec),

      async waitForResult(options) {
        const interval = options?.intervalMs ?? pollIntervalMs;
        const timeout = options?.timeoutMs ?? pollTimeoutMs;
        const signal = options?.signal;
        const start = now().getTime();
        const stopped = () => signal?.aborted === true;
        const aborted = signal
          ? new Promise<void>((resolve) => {
              if (signal.aborted) resolve();
              else signal.addEventListener('abort', () => resolve(), { once: true });
            })
          : null;
        // 멈추면 기다리기만 끝낸다. 기다리던 일(보내기, 상태 확인)은 그대로 돈다
        const until = (job: Promise<unknown>) => {
          const settled = job.then(
            () => undefined,
            () => undefined,
          );
          return aborted ? Promise.race([settled, aborted]) : settled;
        };
        await until(syncer.kick());
        for (;;) {
          if (stopped()) return progress(rec);
          options?.onProgress?.(progress(rec));
          const finalStates: LocalState[] = ['done', 'failed', 'blocked', 'queued', 'recording'];
          if (finalStates.includes(rec.state)) return progress(rec);
          await until(syncer.refresh(rec.id));
          if (stopped()) return progress(rec);
          if (rec.state === 'done' || rec.state === 'failed') {
            options?.onProgress?.(progress(rec));
            return progress(rec);
          }
          if (now().getTime() - start >= timeout) return progress(rec);
          await until(sleep(interval));
        }
      },

      async discard() {
        requireOpen('버리기');
        for (const r of rec.recordings) await deps.store.deleteFile(r.fileUri);
        await repo.removeSession(rec.id);
        log('session_discarded', { session_id: rec.id });
      },
    };
  }

  async function openSession(sessionId: string): Promise<Session | null> {
    const rec = await repo.loadSession(sessionId);
    if (!rec) return null;
    const user = await repo.loadUser(rec.userId);
    if (!user) return null;
    return handle(rec, user);
  }

  return {
    async startSession(purpose, options) {
      const user = await currentUserRecord();
      const rec: SessionRecord = {
        schema: 1,
        id: newId(),
        userId: user.userId,
        purpose,
        startedAt: now().toISOString(),
        endedAt: null,
        device: deps.device(),
        inputDevice: 'unknown',
        sampleRate: SAMPLE_RATE,
        clientMeta: { ...(options?.clientMeta ?? {}), ...(user.dev ? { dev: true } : {}) },
        completeMeta: {},
        recordings: [],
        gameRuns: [],
        deviceMeasurements: [],
        baselines: null,
        state: 'recording',
        sessionRegistered: false,
        completeAccepted: false,
        sendAttempts: 0,
        lastError: null,
        server: null,
        updatedAt: now().toISOString(),
      };
      await repo.saveSession(rec, now());
      log('session_started', { session_id: rec.id, user_id: user.userId, purpose, dev: user.dev });
      return handle(rec, user);
    },

    async resumePending() {
      const unfinished: Session[] = [];
      let resumed = 0;
      for (const id of await repo.listSessionIds()) {
        const rec = await repo.loadSession(id);
        if (!rec) continue;
        if (rec.state === 'sending') {
          rec.state = 'queued';
          await repo.saveSession(rec, now());
        }
        if (rec.state === 'queued') resumed += 1;
        if (rec.state === 'recording') {
          const s = await openSession(id);
          if (s) unfinished.push(s);
        }
      }
      unfinished.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
      const sweptFiles = await sweepOrphans();
      log('resume', { resumed, unfinished: unfinished.length, swept_files: sweptFiles });
      void (async () => {
        await syncer.kick();
        await refreshProcessing();
        await prune();
      })().catch((e) => log('resume_error', { message: String(e) }));
      return { resumed, unfinished, sweptFiles };
    },

    async syncNow() {
      await syncer.kick();
      await refreshProcessing();
    },

    refreshStatuses: () => refreshProcessing(),

    async getLatestBaselines() {
      return (await currentUserRecord()).latestBaselines;
    },

    async getHistory() {
      const user = await currentUserRecord();
      try {
        return await deps.api.getHistory(user.userId);
      } catch (e) {
        if (e instanceof ApiError && e.code === 'USER_NOT_FOUND') return null;
        log('history_error', { ...errorInfo(e, now()) });
        throw e;
      }
    },

    async currentUser() {
      const u = await currentUserRecord();
      return { userId: u.userId, createdAt: u.createdAt, dev: u.dev };
    },

    async startNewDevUser() {
      const u = await createUser(true);
      return { userId: u.userId, createdAt: u.createdAt, dev: u.dev };
    },

    devUserIds: () => repo.devUserIds(),

    async listSessions() {
      const out: SessionSummary[] = [];
      for (const id of await repo.listSessionIds()) {
        const rec = await repo.loadSession(id);
        if (!rec) continue;
        out.push({
          sessionId: rec.id,
          userId: rec.userId,
          purpose: rec.purpose,
          startedAt: rec.startedAt,
          state: rec.state,
          serverStatus: rec.server?.status ?? null,
          lastErrorCode: rec.lastError?.code ?? null,
          uploaded: rec.recordings.filter((r) => r.uploaded).length,
          total: rec.recordings.length,
        });
      }
      return out.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    },

    openSession,
    readRecordingFile: (uri) => deps.store.readFile(uri),
    discardRecordingFile: (uri) => deps.store.deleteFile(uri),
  };
}
