// 기기에 저장하는 문서 형식과 읽기·쓰기. 같은 문서는 메모리에 하나만 두고(캐시) 화면과 보내기 작업이 함께 쓴다.

import type { Store } from './store';
import type {
  Baselines,
  DeviceMeasurement,
  ErrorInfo,
  GameRun,
  HzRange,
  LocalState,
  Purpose,
  RecordingKind,
  SessionDetail,
} from './types';

export interface DeviceInfo {
  device_model: string;
  os_version: string;
  app_version: string;
}

export interface RecordingRecord {
  recordingId: string;
  kind: RecordingKind;
  trialNo?: number;
  sentenceId?: string;
  gameRunId?: string;
  agcSignal?: string;
  fileUri: string;
  durationS: number;
  sampleRate: number;
  sizeBytes: number;
  addedAt: string;
  uploaded: boolean;
}

export interface UserRecord {
  userId: string;
  createdAt: string;
  dev: boolean;
  /** 서버에 사용자 등록(PUT /v1/users)을 마쳤는지 */
  registered: boolean;
  calibration: { f0RangeHz: HzRange; sessionId: string; measuredAt: string } | null;
  latestBaselines: Baselines | null;
}

export interface SessionRecord {
  schema: 1;
  id: string;
  userId: string;
  purpose: Purpose;
  startedAt: string;
  endedAt: string | null;
  device: DeviceInfo;
  inputDevice: string;
  sampleRate: number;
  /** 세션 등록 때 보내는 client_meta */
  clientMeta: Record<string, unknown>;
  /** complete 때 보내는 client_meta */
  completeMeta: Record<string, unknown>;
  recordings: RecordingRecord[];
  gameRuns: GameRun[];
  deviceMeasurements: DeviceMeasurement[];
  baselines: Baselines | null;
  state: LocalState;
  sessionRegistered: boolean;
  completeAccepted: boolean;
  sendAttempts: number;
  lastError: ErrorInfo | null;
  server: SessionDetail | null;
  updatedAt: string;
}

const CURRENT_USER = 'current-user.json';
const DEV_USERS = 'dev-users.json';
const userPath = (id: string) => `users/${id}.json`;
const sessionPath = (id: string) => `sessions/${id}.json`;

export class Repo {
  private readonly store: Store;
  private readonly sessions = new Map<string, SessionRecord>();
  private readonly users = new Map<string, UserRecord>();
  private readonly writes = new Map<string, Promise<void>>();

  constructor(store: Store) {
    this.store = store;
  }

  async loadSession(id: string): Promise<SessionRecord | null> {
    const cached = this.sessions.get(id);
    if (cached) return cached;
    const doc = (await this.store.readJson(sessionPath(id))) as SessionRecord | null;
    if (!doc) return null;
    const again = this.sessions.get(id);
    if (again) return again;
    this.sessions.set(id, doc);
    return doc;
  }

  async saveSession(rec: SessionRecord, now: Date): Promise<void> {
    rec.updatedAt = now.toISOString();
    this.sessions.set(rec.id, rec);
    await this.write(sessionPath(rec.id), rec);
  }

  async removeSession(id: string): Promise<void> {
    this.sessions.delete(id);
    await this.chain(sessionPath(id), () => this.store.remove(sessionPath(id)));
  }

  async listSessionIds(): Promise<string[]> {
    const names = await this.store.list('sessions');
    const ids = new Set(names.filter((n) => n.endsWith('.json')).map((n) => n.slice(0, -5)));
    for (const id of this.sessions.keys()) ids.add(id);
    return [...ids];
  }

  async loadUser(id: string): Promise<UserRecord | null> {
    const cached = this.users.get(id);
    if (cached) return cached;
    const doc = (await this.store.readJson(userPath(id))) as UserRecord | null;
    if (!doc) return null;
    const again = this.users.get(id);
    if (again) return again;
    this.users.set(id, doc);
    return doc;
  }

  async saveUser(user: UserRecord): Promise<void> {
    this.users.set(user.userId, user);
    await this.write(userPath(user.userId), user);
  }

  async currentUserId(): Promise<string | null> {
    const doc = (await this.store.readJson(CURRENT_USER)) as { userId?: string } | null;
    return doc?.userId ?? null;
  }

  async setCurrentUser(id: string): Promise<void> {
    await this.write(CURRENT_USER, { userId: id });
  }

  async devUserIds(): Promise<string[]> {
    const doc = (await this.store.readJson(DEV_USERS)) as { userIds?: string[] } | null;
    return doc?.userIds ?? [];
  }

  async addDevUser(id: string): Promise<void> {
    const ids = await this.devUserIds();
    if (!ids.includes(id)) await this.write(DEV_USERS, { userIds: [...ids, id] });
  }

  /** 같은 경로의 쓰기는 들어온 순서대로 하나씩 한다 */
  private write(path: string, value: unknown): Promise<void> {
    const snapshot = JSON.parse(JSON.stringify(value));
    return this.chain(path, () => this.store.writeJson(path, snapshot));
  }

  private chain(path: string, job: () => Promise<void>): Promise<void> {
    const previous = this.writes.get(path) ?? Promise.resolve();
    const next = previous.then(job, job);
    const settled = next.catch(() => undefined);
    this.writes.set(path, settled);
    void settled.then(() => {
      if (this.writes.get(path) === settled) this.writes.delete(path);
    });
    return next;
  }
}
