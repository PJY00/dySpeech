// 앱 화면 흐름(순수 함수). 세션 객체·기준값은 화면(AppShell)이 들고, 여기서는 어느 화면인지와 라운드 번호만 다룬다.
// - 오늘 훈련: (첫 회차면 캘리브레이션) 측정 블록 → 게임 선택 → 라운드 반복 → 오늘 훈련 끝 → complete → 결과
// - 반복 측정: 측정 블록 → complete → 결과

import type { GameName } from './gameTypes';
import type { Baselines, GameRun, Purpose } from '../session/types';

export type Screen =
  | { name: 'home' }
  | { name: 'resume' }
  | { name: 'measure' }
  | { name: 'choose' }
  | { name: 'game'; game: GameName; level: number; roundIndex: number }
  | { name: 'roundEnd'; game: GameName; run: GameRun; nextLevel: number }
  | { name: 'finishing' }
  | { name: 'result' }
  | { name: 'records' };

export interface FlowState {
  screen: Screen;
  purpose: Purpose | null;
  /** 이번 회차에서 게임별로 끝낸 라운드 수 */
  rounds: Partial<Record<GameName, number>>;
}

export type FlowAction =
  | { type: 'unfinished' }
  | { type: 'start'; purpose: 'training' | 'retest' }
  | { type: 'measured' }
  | { type: 'cancel' }
  | { type: 'pickGame'; game: GameName; level: number }
  | { type: 'roundEnded'; run: GameRun; nextLevel: number }
  | { type: 'nextRound' }
  | { type: 'chooseOther' }
  | { type: 'finish' }
  | { type: 'finished' }
  | { type: 'records' }
  /** 메인의 최근 회차 카드를 눌러 그 회차 결과 화면을 연다 */
  | { type: 'openResult' }
  | { type: 'home' };

export const initialFlow = (screen: Screen = { name: 'home' }, purpose: Purpose | null = null): FlowState => ({
  screen,
  purpose,
  rounds: {},
});

export function reduceFlow(state: FlowState, action: FlowAction): FlowState {
  const s = state.screen;
  switch (action.type) {
    case 'unfinished':
      return s.name === 'home' ? { ...state, screen: { name: 'resume' } } : state;
    case 'start':
      return { screen: { name: 'measure' }, purpose: action.purpose, rounds: {} };
    case 'measured':
      if (s.name !== 'measure') return state;
      return { ...state, screen: state.purpose === 'training' ? { name: 'choose' } : { name: 'finishing' } };
    case 'cancel':
      return initialFlow();
    case 'pickGame':
      if (s.name !== 'choose') return state;
      return { ...state, screen: { name: 'game', game: action.game, level: action.level, roundIndex: (state.rounds[action.game] ?? 0) + 1 } };
    case 'roundEnded': {
      if (s.name !== 'game') return state;
      const rounds = { ...state.rounds, [s.game]: (state.rounds[s.game] ?? 0) + 1 };
      return { ...state, rounds, screen: { name: 'roundEnd', game: s.game, run: action.run, nextLevel: action.nextLevel } };
    }
    case 'nextRound':
      if (s.name !== 'roundEnd') return state;
      return { ...state, screen: { name: 'game', game: s.game, level: s.nextLevel, roundIndex: (state.rounds[s.game] ?? 0) + 1 } };
    case 'chooseOther':
      return s.name === 'roundEnd' || s.name === 'game' ? { ...state, screen: { name: 'choose' } } : state;
    case 'finish':
      return s.name === 'choose' || s.name === 'roundEnd' ? { ...state, screen: { name: 'finishing' } } : state;
    case 'finished':
      return s.name === 'finishing' ? { ...state, screen: { name: 'result' } } : state;
    case 'records':
      return s.name === 'home' ? { ...state, screen: { name: 'records' } } : state;
    case 'openResult':
      return s.name === 'home' ? { ...initialFlow(), screen: { name: 'result' } } : state;
    case 'home':
      return initialFlow();
  }
}

/** 이번 회차에 끝낸 라운드 수 합 */
export function totalRounds(state: FlowState): number {
  return Object.values(state.rounds).reduce<number>((a, b) => a + (b ?? 0), 0);
}

/**
 * 끝나지 않은 회차(앱이 complete 전에 꺼짐)에 보여 줄 선택. 훈련·반복 측정 모두 같은 규칙
 * - 측정 블록을 끝내지 못한 회차(기준값 없음): 버리기만. 덜 끝난 측정이 진전도 기준선이 되지 않게 한다
 * - 측정 블록을 끝낸 회차: 보내기·버리기
 */
export function unfinishedOptions(session: { baselines(): Baselines | null }): { canSend: boolean; reason: string | null } {
  return session.baselines() === null
    ? { canSend: false, reason: '측정을 끝내지 못한 회차는 보낼 수 없어요' }
    : { canSend: true, reason: null };
}
