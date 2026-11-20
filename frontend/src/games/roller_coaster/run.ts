import type { SpaceshipGameRun } from '../spaceship/metrics';

export type RollerCoasterGameRun = Omit<SpaceshipGameRun, 'game'> & { game: 'roller_coaster' };

export function toRollerCoasterRun(run: SpaceshipGameRun): RollerCoasterGameRun {
  return { ...run, game: 'roller_coaster' };
}
