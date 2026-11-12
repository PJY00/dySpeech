import type { GameRun } from '../../session/types';

export interface BalloonGameProps {
  /** 기준값(측정 블록 결과). 구하지 못한 값은 null이고, 그때는 게임의 기본값을 쓴다 */
  baselines: {
    /** 잡음 바닥(dBFS) */
    noiseFloorDbfs: number | null;
    /** "아" 음량(dBFS). 호흡 훈련의 기준 음량 */
    sustainedLevelDbfs: number | null;
    /** 최대발성지속시간(초). 호흡 훈련의 목표 유지 시간을 정하는 기준 */
    maxPhonationTimeS?: number | null;
  };
  level: number;
  roundIndex: number;
  onRoundEnd: (run: GameRun, recording?: { fileUri: string }) => void;
  onExit: () => void;
}
