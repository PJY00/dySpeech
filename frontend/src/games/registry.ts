// 게임 등록부. 게임 선택 화면이 여기서 영역별(호흡 / 말속도 / 억양)로 읽어 보여 준다.
// 게임을 붙일 때 GAMES에 한 줄씩 추가한다. 약속은 src/games/README.md

import { L } from '../i18n/lang';
import type { GameArea, GameEntry, GameName } from '../training/gameTypes';
import { BalloonGameScreen, CandleGameScreen, MAX_LEVEL as BREATH_MAX_LEVEL } from './balloon';
import { describeBalloonRun } from './balloon/describe';
import { describeGuideReadingRun } from './guide_reading/describe';
import { describeRollerCoasterRun } from './roller_coaster/describe';
import { describeSpaceshipRun } from './spaceship/describe';
import { GuideReadingGame, MAX_LEVEL as GUIDE_READING_MAX_LEVEL } from './guide_reading';
import { MAX_LEVEL as ROLLER_COASTER_MAX_LEVEL, RollerCoasterRound } from './roller_coaster';
import { MAX_LEVEL as SPACESHIP_MAX_LEVEL, SpaceshipRound } from './spaceship';

export { GAME_AREAS, GAME_NAMES } from '../training/gameTypes';
export type { GameArea, GameBaselines, GameEntry, GameName, GameProps, RoundRecording } from '../training/gameTypes';

/** 이름·설명은 [한국어, 영어]. 화면 언어에 맞춰 읽힌다 */
function entry(
  base: Omit<GameEntry, 'title' | 'description'>,
  title: [string, string],
  description: [string, string],
): GameEntry {
  return {
    ...base,
    get title() {
      return L(...title);
    },
    get description() {
      return L(...description);
    },
  };
}

export const GAMES: readonly GameEntry[] = [
  entry(
    { game: 'balloon', area: 'breath', maxLevel: BREATH_MAX_LEVEL, component: BalloonGameScreen, describeRun: describeBalloonRun },
    ['풍선 불기', 'Blow Up the Balloon'],
    ['소리를 내서 풍선을 키워요', 'Make sound to inflate the balloon'],
  ),
  entry(
    { game: 'candle', area: 'breath', maxLevel: BREATH_MAX_LEVEL, component: CandleGameScreen, describeRun: describeBalloonRun },
    ['촛불 지키기', 'Keep the Candle Lit'],
    ['소리를 계속 내서 촛불을 지켜요', 'Keep making sound to keep the candle burning'],
  ),
  entry(
    { game: 'guide_reading', area: 'rate', maxLevel: GUIDE_READING_MAX_LEVEL, component: GuideReadingGame, describeRun: describeGuideReadingRun },
    ['속도 맞춰 읽기', 'Pace Reading'],
    ['파란 안내를 따라 같은 빠르기로 읽어요', 'Follow the blue guide and read at the same pace'],
  ),
  entry(
    { game: 'spaceship', area: 'intonation', maxLevel: SPACESHIP_MAX_LEVEL, component: SpaceshipRound, describeRun: describeSpaceshipRun },
    ['우주선 게임', 'Spaceship'],
    ['목소리 높낮이로 우주선을 움직여 길을 따라가요', 'Move the ship with your voice pitch and follow the path'],
  ),
  entry(
    { game: 'roller_coaster', area: 'intonation', maxLevel: ROLLER_COASTER_MAX_LEVEL, component: RollerCoasterRound, describeRun: describeRollerCoasterRun },
    ['롤러코스터', 'Roller Coaster'],
    ['목소리로 레일을 깔고 함께 달려요', 'Lay the track with your voice and ride along'],
  ),
];

export function gamesInArea(area: GameArea): GameEntry[] {
  return GAMES.filter((g) => g.area === area);
}

export function findGame(game: GameName | string): GameEntry | undefined {
  return GAMES.find((g) => g.game === game);
}
