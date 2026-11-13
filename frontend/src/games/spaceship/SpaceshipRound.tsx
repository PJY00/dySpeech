import type { GameProps } from '../../app/gameTypes';
import SpaceshipGame from './SpaceshipGame';

const DEFAULT_F0_HZ = 150;
const DEFAULT_NOISE_FLOOR_DBFS = -60;

export function SpaceshipRound({ baselines, level, roundIndex, onRoundEnd, onExit }: GameProps) {
  const baseline = {
    f0MedianHz: baselines.f0MedianHz ?? DEFAULT_F0_HZ,
    noiseFloorDbfs: baselines.noiseFloorDbfs ?? DEFAULT_NOISE_FLOOR_DBFS,
    f0RangeHz: baselines.f0SearchRangeHz,
  };
  return (
    <SpaceshipGame
      baseline={baseline}
      level={level}
      roundIndex={roundIndex}
      onFinish={(run) => onRoundEnd(run)}
      onExit={onExit}
    />
  );
}
