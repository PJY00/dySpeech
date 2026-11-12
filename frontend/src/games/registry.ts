// 게임 등록부. 게임 선택 화면이 여기서 영역별(호흡 / 말속도 / 억양)로 읽어 보여 준다.
// 게임을 붙일 때 GAMES에 한 줄씩 추가한다. 약속은 src/games/README.md

import type { GameArea, GameEntry, GameName } from '../training/gameTypes';
import { BalloonGameScreen, CandleGameScreen, MAX_LEVEL as BREATH_MAX_LEVEL } from './balloon';
import { GuideReadingGame, MAX_LEVEL as GUIDE_READING_MAX_LEVEL } from './guide_reading';

export { GAME_AREAS, GAME_NAMES } from '../training/gameTypes';
export type { GameArea, GameBaselines, GameEntry, GameName, GameProps, RoundRecording } from '../training/gameTypes';

export const GAMES: readonly GameEntry[] = [
  { game: 'balloon', area: 'breath', title: '풍선 불기', description: '소리를 내서 풍선을 키워요', maxLevel: BREATH_MAX_LEVEL, component: BalloonGameScreen },
  { game: 'candle', area: 'breath', title: '촛불 지키기', description: '소리를 이어서 내서 촛불을 지켜요', maxLevel: BREATH_MAX_LEVEL, component: CandleGameScreen },
  { game: 'guide_reading', area: 'rate', title: '노래방 따라 읽기', description: '파란 안내를 따라 같은 빠르기로 읽어요', maxLevel: GUIDE_READING_MAX_LEVEL, component: GuideReadingGame },
];

export function gamesInArea(area: GameArea): GameEntry[] {
  return GAMES.filter((g) => g.area === area);
}

export function findGame(game: GameName | string): GameEntry | undefined {
  return GAMES.find((g) => g.game === game);
}
