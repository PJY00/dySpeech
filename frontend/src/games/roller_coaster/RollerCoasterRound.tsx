import type { GameProps } from '../../training/gameTypes';
import RollerCoasterGame from './RollerCoasterGame';

const DEFAULT_F0_HZ = 150;
const DEFAULT_NOISE_FLOOR_DBFS = -60;

export function RollerCoasterRound({ baselines, level, roundIndex, onRoundEnd, onExit }: GameProps) {
  const baseline = {
    f0MedianHz: baselines.f0MedianHz ?? DEFAULT_F0_HZ,
    noiseFloorDbfs: baselines.noiseFloorDbfs ?? DEFAULT_NOISE_FLOOR_DBFS,
    f0RangeHz: baselines.f0SearchRangeHz,
  };
  return (
    <RollerCoasterGame
      baseline={baseline}
      level={level}
      roundIndex={roundIndex}
      onFinish={(run) => onRoundEnd(run)}
      onExit={onExit}
    />
  );
}
