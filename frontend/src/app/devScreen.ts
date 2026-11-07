// 개발용 바로가기 EXPO_PUBLIC_SCREEN(frontend/.env). 비우면 앱 기본 화면.
// 개발 빌드(__DEV__)에서만 듣는다. 릴리스 APK에 값이 남아 있어도 앱 기본 화면을 연다(바로가기는 서버로 보내지 않으므로).
// - measurement-test : 측정 블록 시험 화면(반복 측정 세션으로 서버까지)
// - game-select      : 임시 기준값으로 게임 선택 화면부터(서버로 보내지 않음)
// - game:<게임 이름>  : 임시 기준값으로 그 게임부터(서버로 보내지 않음). 예: game:guide_reading

import type { GameBaselines, GameName } from './gameTypes';
import { GAME_NAMES } from './gameTypes';

export type DevScreen =
  | { kind: 'app' }
  | { kind: 'measurement-test' }
  | { kind: 'game-select' }
  | { kind: 'game'; game: GameName }
  | { kind: 'unknown'; value: string };

export function parseDevScreen(value: string | undefined | null): DevScreen {
  const v = (value ?? '').trim();
  if (!v) return { kind: 'app' };
  if (v === 'measurement-test') return { kind: 'measurement-test' };
  if (v === 'game-select') return { kind: 'game-select' };
  if (v.startsWith('game:')) {
    const name = v.slice('game:'.length);
    if ((GAME_NAMES as readonly string[]).includes(name)) return { kind: 'game', game: name as GameName };
  }
  return { kind: 'unknown', value: v };
}

/** 개발 빌드일 때만 바로가기를 해석한다. 릴리스(isDev = false)는 값과 상관없이 앱 기본 화면 */
export function resolveDevScreen(value: string | undefined | null, isDev: boolean): DevScreen {
  return isDev ? parseDevScreen(value) : { kind: 'app' };
}

/** 바로가기에서 쓰는 임시 기준값(측정 블록을 건너뛸 때) */
export const TEMP_BASELINES: GameBaselines = {
  noiseFloorDbfs: -60,
  sustainedLevelDbfs: -25,
  f0MedianHz: 150,
  f0SearchRangeHz: { min: 75, max: 500 },
  maxPhonationTimeS: 10,
};
