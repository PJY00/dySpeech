// 게임 등록부. 게임 선택 화면이 여기서 영역별(호흡 / 말속도 / 억양)로 읽어 보여 준다.
// 게임을 붙일 때 GAMES에 한 줄씩 추가한다. 약속은 src/games/README.md

import type { GameArea, GameEntry, GameName } from '../app/gameTypes';

export { GAME_AREAS, GAME_NAMES } from '../app/gameTypes';
export type { GameArea, GameBaselines, GameEntry, GameName, GameProps, RoundRecording } from '../app/gameTypes';

export const GAMES: readonly GameEntry[] = [];

export function gamesInArea(area: GameArea): GameEntry[] {
  return GAMES.filter((g) => g.area === area);
}

export function findGame(game: GameName | string): GameEntry | undefined {
  return GAMES.find((g) => g.game === game);
}
