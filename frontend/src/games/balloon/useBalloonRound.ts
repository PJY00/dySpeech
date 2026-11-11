import { useRef, useState } from 'react';

import {
  BalloonState,
  getLevel,
  initialBalloonState,
  isRoundOver,
  stepBalloon,
} from './balloonLogic';
import { BalloonGameName, BalloonGameRun, buildBalloonRun } from './balloonRun';
import type { BalloonGameProps } from './gameProps';
import { useRoundInput, VOICED_MARGIN_DB } from './roundInput';

/** 측정 블록에서 기준값을 구하지 못했을 때 쓰는 기본값(dBFS) */
export const DEFAULT_SUSTAINED_DBFS = -25;
export const DEFAULT_NOISE_FLOOR_DBFS = -60;

/**
 * loading: 마이크 준비 / denied: 마이크 권한 거부 / ready: 시작 전 / playing: 진행 중
 * finished: 통과 또는 시간 종료(결과를 보여 주는 중) / error: 마이크 오류
 */
export type RoundPhase = 'loading' | 'denied' | 'ready' | 'playing' | 'finished' | 'error';

type Options = Pick<BalloonGameProps, 'baselines' | 'level' | 'roundIndex' | 'onRoundEnd' | 'onExit'> & {
  game: BalloonGameName;
  /** 프레임을 처리할 때마다 호출 (화면 전용 효과용) */
  onStep?: (state: BalloonState, dt: number) => void;
};

export function useBalloonRound({ game, baselines, level, roundIndex, onRoundEnd, onExit, onStep }: Options) {
  const [roundPhase, setRoundPhase] = useState<'ready' | 'playing' | 'finished'>('ready');
  const [state, setState] = useState<BalloonState>(initialBalloonState);
  const [dbDiff, setDbDiff] = useState(0);
  const [lastRun, setLastRun] = useState<BalloonGameRun | null>(null);

  const phaseRef = useRef<'ready' | 'playing' | 'finished'>('ready');
  const stateRef = useRef<BalloonState>(initialBalloonState);
  const diffsRef = useRef<number[]>([]);
  const startedAtRef = useRef(new Date().toISOString());
  const finishedRef = useRef(false);
  const stopRef = useRef<() => Promise<void>>(async () => {});
  const baselinesRef = useRef(baselines);
  baselinesRef.current = baselines;
  const mptRef = useRef(baselines.maxPhonationTimeS);
  mptRef.current = baselines.maxPhonationTimeS;
  const levelRef = useRef(level);
  levelRef.current = level;
  const roundRef = useRef(roundIndex);
  roundRef.current = roundIndex;
  const onStepRef = useRef(onStep);
  onStepRef.current = onStep;
  const onRoundEndRef = useRef(onRoundEnd);
  onRoundEndRef.current = onRoundEnd;
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;

  const changePhase = (next: 'ready' | 'playing' | 'finished') => {
    phaseRef.current = next;
    setRoundPhase(next);
  };

  function finishRound() {
    if (finishedRef.current) return;
    finishedRef.current = true;
    changePhase('finished');
    const b = baselinesRef.current;
    setLastRun(
      buildBalloonRun({
        game,
        state: stateRef.current,
        diffsDb: diffsRef.current,
        level: levelRef.current,
        roundIndex: roundRef.current,
        startedAt: startedAtRef.current,
        target: getLevel(levelRef.current, mptRef.current),
        baselineDb: b.sustainedLevelDbfs ?? DEFAULT_SUSTAINED_DBFS,
        noiseFloorDbfs: b.noiseFloorDbfs ?? DEFAULT_NOISE_FLOOR_DBFS,
        voicedMarginDb: VOICED_MARGIN_DB,
      }),
    );
    void stopRef.current();
  }

  function handleLevel(levelDbfs: number, dt: number) {
    if (phaseRef.current !== 'playing' || finishedRef.current) return;
    const b = baselinesRef.current;
    const baselineDb = b.sustainedLevelDbfs ?? DEFAULT_SUSTAINED_DBFS;
    const noiseFloor = b.noiseFloorDbfs ?? DEFAULT_NOISE_FLOOR_DBFS;
    const diff = levelDbfs - baselineDb;
    const voiced = levelDbfs >= noiseFloor + VOICED_MARGIN_DB;

    stateRef.current = stepBalloon(stateRef.current, diff, dt, getLevel(levelRef.current, mptRef.current), voiced);
    if (voiced) diffsRef.current.push(diff);
    setDbDiff(diff);
    setState(stateRef.current);
    onStepRef.current?.(stateRef.current, dt);

    if (isRoundOver(stateRef.current)) finishRound();
  }

  const input = useRoundInput(handleLevel);
  stopRef.current = input.stop;

  const phase: RoundPhase = input.status === 'ready' ? roundPhase : input.status;

  const start = async () => {
    stateRef.current = initialBalloonState;
    diffsRef.current = [];
    finishedRef.current = false;
    startedAtRef.current = new Date().toISOString();
    setState(initialBalloonState);
    setDbDiff(0);
    setLastRun(null);
    changePhase('playing');
    await input.start();
  };

  const confirm = () => {
    if (lastRun) onRoundEndRef.current(lastRun);
  };

  const exit = async () => {
    finishedRef.current = true;
    await input.stop();
    onExitRef.current();
  };

  return { phase, state, dbDiff, target: getLevel(level, baselines.maxPhonationTimeS), lastRun, error: input.error, start, confirm, exit };
}
