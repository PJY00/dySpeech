// 게임 등록부. 게임 선택 화면이 여기서 영역별(호흡 / 말속도 / 억양)로 읽어 보여 준다.
// 게임을 붙일 때 GAMES에 한 줄씩 추가한다. 약속은 src/games/README.md

import type { GameArea, GameEntry, GameName } from '../training/gameTypes';
import { BalloonGameScreen, CandleGameScreen, MAX_LEVEL as BREATH_MAX_LEVEL } from './balloon';
import { GuideReadingGame, MAX_LEVEL as GUIDE_READING_MAX_LEVEL } from './guide_reading';
import { MAX_LEVEL as SPACESHIP_MAX_LEVEL, SpaceshipRound } from './spaceship';

export { GAME_AREAS, GAME_NAMES } from '../training/gameTypes';
export type { GameArea, GameBaselines, GameEntry, GameName, GameProps, RoundRecording } from '../training/gameTypes';

export const GAMES: readonly GameEntry[] = [
  { game: 'balloon', area: 'breath', title: 'Blow Up the Balloon', description: 'Make sound to inflate the balloon', maxLevel: BREATH_MAX_LEVEL, component: BalloonGameScreen },
  { game: 'candle', area: 'breath', title: 'Keep the Candle Lit', description: 'Keep making sound to keep the candle burning', maxLevel: BREATH_MAX_LEVEL, component: CandleGameScreen },
  { game: 'guide_reading', area: 'rate', title: 'Pace Reading', description: 'Follow the blue guide and read at the same pace', maxLevel: GUIDE_READING_MAX_LEVEL, component: GuideReadingGame },
  { game: 'spaceship', area: 'intonation', title: 'Spaceship', description: 'Move the ship with your voice pitch and follow the path', maxLevel: SPACESHIP_MAX_LEVEL, component: SpaceshipRound },
];

export function gamesInArea(area: GameArea): GameEntry[] {
  return GAMES.filter((g) => g.area === area);
}

export function findGame(game: GameName | string): GameEntry | undefined {
  return GAMES.find((g) => g.game === game);
}
