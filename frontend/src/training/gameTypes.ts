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
 * - f0SearchRangeHz는 항상 있다(캘리브레이션이 없으면 75~500 Hz)
 */
export type GameBaselines = Pick<
  Baselines,
  'noiseFloorDbfs' | 'sustainedLevelDbfs' | 'f0MedianHz' | 'f0SearchRangeHz' | 'maxPhonationTimeS' | 'articulationRateSps'
>;

export function gameBaselines(b: Baselines): GameBaselines {
  return {
    noiseFloorDbfs: b.noiseFloorDbfs,
    sustainedLevelDbfs: b.sustainedLevelDbfs,
    f0MedianHz: b.f0MedianHz,
    f0SearchRangeHz: b.f0SearchRangeHz,
    maxPhonationTimeS: b.maxPhonationTimeS,
    articulationRateSps: b.articulationRateSps,
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
