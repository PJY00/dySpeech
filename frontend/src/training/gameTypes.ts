// 게임 연결 약속(src/games/README.md). 앱 화면 구조가 게임에 주는 값과 게임이 돌려주는 값.

import type { ComponentType } from 'react';

import type { Baselines, GameRun } from '../session/types';

/** 게임 이름(api.md 7.2의 game). 영문 소문자 snake_case */
export type GameName = 'balloon' | 'candle' | 'spaceship' | 'roller_coaster' | 'guide_reading';
export const GAME_NAMES: readonly GameName[] = ['balloon', 'candle', 'spaceship', 'roller_coaster', 'guide_reading'];

/** 게임 선택 화면의 영역 */
export type GameArea = 'breath' | 'rate' | 'intonation';
export const GAME_AREAS: readonly { area: GameArea; title: string; description: string }[] = [
  { area: 'breath', title: '호흡', description: '숨을 길게 쓰고 소리를 고르게 내요' },
  { area: 'rate', title: '말속도', description: '알맞은 빠르기로 또박또박 읽어요' },
  { area: 'intonation', title: '억양', description: '목소리 높낮이를 살려 말해요' },
];

/**
 * 게임이 받는 기준값(측정 블록에서 기기가 계산). 구하지 못한 값은 null
 * - "아" 음량, 기본주파수 중앙값, 최대발성지속시간, 조음속도는 오늘을 포함한 최근 3회차 값의 중앙값이다(gameBaselines)
 * - 잡음 바닥은 그날 값이다(그날 방의 소음이라서)
 * - f0SearchRangeHz는 항상 있다(캘리브레이션이 없으면 75~500 Hz)
 */
export type GameBaselines = Pick<
  Baselines,
  'noiseFloorDbfs' | 'sustainedLevelDbfs' | 'f0MedianHz' | 'f0SearchRangeHz' | 'maxPhonationTimeS' | 'articulationRateSps'
>;

/** 게임 기준값에 쓰는 회차 수(오늘 포함) */
export const RECENT_SESSIONS = 3;

/** 중앙값. 값이 짝수 개면 가운데 두 값의 평균, 없으면 null */
export function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * 게임 기준값을 만든다. 한 번 잰 값의 흔들림이 그날 목표에 그대로 들어가지 않게, 오늘을 포함한 최근 3회차의 중앙값을 쓴다
 * - 대상: "아" 음량, 기본주파수 중앙값, 최대발성지속시간, 조음속도
 * - 최근 3회차 = 오늘 + 그 앞의 훈련 회차 2개. 1회차는 오늘 값, 2회차는 두 값의 평균
 * - 그 3회차 안에서 값이 있는 것만으로 구한다. 하나도 없으면 null(게임이 기본값을 씀)
 * - 잡음 바닥과 기본주파수 탐색 범위는 오늘 값 그대로
 * earlier: 지난 훈련 회차의 기준값(listTrainingBaselines). 순서는 상관없고, 오늘 회차가 섞여 있으면 뺀다
 */
export function gameBaselines(today: Baselines, earlier: readonly Baselines[] = []): GameBaselines {
  const before = earlier
    .filter((b) => b.sessionId !== today.sessionId)
    .sort((a, b) => b.measuredAt.localeCompare(a.measuredAt))
    .slice(0, RECENT_SESSIONS - 1);
  const recent = [today, ...before];
  const recentMedian = (pick: (b: Baselines) => number | null | undefined): number | null =>
    median(recent.map(pick).filter((v): v is number => typeof v === 'number' && Number.isFinite(v)));
  return {
    noiseFloorDbfs: today.noiseFloorDbfs,
    sustainedLevelDbfs: recentMedian((b) => b.sustainedLevelDbfs),
    f0MedianHz: recentMedian((b) => b.f0MedianHz),
    f0SearchRangeHz: today.f0SearchRangeHz,
    maxPhonationTimeS: recentMedian((b) => b.maxPhonationTimeS),
    articulationRateSps: recentMedian((b) => b.articulationRateSps),
  };
}

/** 라운드 녹음(선택). WAV_FILE_OPTIONS(src/measurement)로 녹음한 파일 */
export interface RoundRecording {
  fileUri: string;
}

export interface GameProps {
  /** 기준값(측정 블록 결과) */
  baselines: GameBaselines;
  /** 현재 단계(1부터). 앱이 게임별로 저장해 두었다가 준다 */
  level: number;
  /** 이번 회차에서 이 게임의 몇 번째 라운드인지(1부터). GameRun.round_index에 그대로 넣는다 */
  roundIndex: number;
  /** 게임이 자기 상태(예: 기준값)를 사용자별로 저장할 때 쓰는 키. 개발용 바로가기는 'dev'(실제 사용자 기록과 섞이지 않게) */
  userKey: string;
  /** 라운드가 끝날 때 한 번 부른다. 앱이 세션에 넣고 다음 단계를 정한다 */
  onRoundEnd: (run: GameRun, recording?: RoundRecording) => void;
  /** 라운드를 마치지 않고 나간다(기록 없음) */
  onExit: () => void;
}

export interface GameEntry {
  game: GameName;
  area: GameArea;
  title: string;
  description: string;
  /** 가장 높은 단계 */
  maxLevel: number;
  component: ComponentType<GameProps>;
}
