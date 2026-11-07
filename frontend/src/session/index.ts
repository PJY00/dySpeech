// 앱에서 쓰는 세션 모듈. 사용법은 README.md.
// 서버 주소와 앱 키는 EXPO_PUBLIC_API_URL, EXPO_PUBLIC_APP_KEY(frontend/.env)에서 읽는다.

import { AppState, Platform } from 'react-native';

import appConfig from '../../app.json';
import { ApiClient } from './api';
import { createSessionModule } from './core';
import type { DeviceInfo } from './records';
import { ExpoStore } from './store.expo';

export { RecordingRejected, SAMPLE_RATE } from './core';
export type { Session, SessionModule } from './core';
export { uuidv4, isUuidV4 } from './uuid';
export * from './types';

const API_URL = process.env.EXPO_PUBLIC_API_URL ?? '';
const APP_KEY = process.env.EXPO_PUBLIC_APP_KEY ?? '';
const APP_VERSION = appConfig.expo.version;

function deviceInfo(): DeviceInfo {
  const c = Platform.constants as unknown as Record<string, unknown>;
  if (Platform.OS === 'android') {
    return {
      device_model: String(c.Model ?? 'unknown').slice(0, 100),
      os_version: `Android ${String(c.Release ?? Platform.Version)}`.slice(0, 100),
      app_version: APP_VERSION,
    };
  }
  return {
    device_model: String(c.interfaceIdiom ?? Platform.OS).slice(0, 100),
    os_version: `${Platform.OS} ${String(Platform.Version)}`.slice(0, 100),
    app_version: APP_VERSION,
  };
}

const api = new ApiClient({ baseUrl: API_URL, appKey: APP_KEY, fetch: (url, init) => fetch(url, init as RequestInit) });

export const sessionModule = createSessionModule({
  store: new ExpoStore(),
  api,
  apiConfigured: Boolean(API_URL && APP_KEY),
  device: deviceInfo,
});

export const startSession = sessionModule.startSession;
export const resumePending = sessionModule.resumePending;
export const syncNow = sessionModule.syncNow;
export const getLatestBaselines = sessionModule.getLatestBaselines;
export const getHistory = sessionModule.getHistory;
export const currentUser = sessionModule.currentUser;
export const startNewDevUser = sessionModule.startNewDevUser;
export const devUserIds = sessionModule.devUserIds;
export const listSessions = sessionModule.listSessions;
export const openSession = sessionModule.openSession;
export const readRecordingFile = sessionModule.readRecordingFile;
export const discardRecordingFile = sessionModule.discardRecordingFile;

/** 앱이 앞으로 올 때마다 못 보낸 세션을 보낸다. 끄는 함수를 돌려준다 */
export function startAutoSync(): () => void {
  const sub = AppState.addEventListener('change', (state) => {
    if (state === 'active') void syncNow().catch((e) => console.log('[session] sync_error', String(e)));
  });
  return () => sub.remove();
}

/** 서버 주소와 앱 키가 설정됐는지(개발 화면 표시용) */
export const apiConfig = { url: API_URL, hasKey: Boolean(APP_KEY) };
