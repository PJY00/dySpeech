import { useEffect, useRef, useState } from 'react';

import {
  BalloonState,
  getLevel,
  initialState,
  isRoundOver,
  MODE_OF_GAME,
  stepBalloon,
} from './balloonLogic';
import { BalloonGameName, BalloonGameRun, buildBalloonRun } from './balloonRun';
import type { BalloonGameProps } from './gameProps';
import { useRoundInput, VOICED_MARGIN_DB } from './roundInput';

/** 측정 블록에서 기준값을 구하지 못했을 때 쓰는 기본값(dBFS) */
export const DEFAULT_SUSTAINED_DBFS = -25;
export const DEFAULT_NOISE_FLOOR_DBFS = -60;

/** 라운드가 끝난 뒤 결과 요약을 보여 주기 전에 풍선·불꽃의 마지막 모습을 보여 주는 시간(밀리초) */
export const ENDING_MS = 1500;

/**
 * loading: 마이크 준비 / denied: 마이크 권한 거부 / ready: 시작 전 / playing: 진행 중
 * ending: 끝난 직후 마지막 모습을 잠깐 보여 주는 중 / finished: 결과 요약을 보여 주는 중 / error: 마이크 오류
 */
export type RoundPhase = 'loading' | 'denied' | 'ready' | 'playing' | 'ending' | 'finished' | 'error';
type LocalPhase = 'ready' | 'playing' | 'ending' | 'finished';

type Options = Pick<BalloonGameProps, 'baselines' | 'level' | 'roundIndex' | 'onRoundEnd' | 'onExit'> & {
  game: BalloonGameName;
  /** 프레임을 처리할 때마다 호출 (화면 전용 효과용) */
  onStep?: (state: BalloonState, dt: number) => void;
};

export function useBalloonRound({ game, baselines, level, roundIndex, onRoundEnd, onExit, onStep }: Options) {
  const [roundPhase, setRoundPhase] = useState<LocalPhase>('ready');
  const mode = MODE_OF_GAME[game];
  const [state, setState] = useState<BalloonState>(() => initialState(mode));
  const [dbDiff, setDbDiff] = useState(0);
  const [lastRun, setLastRun] = useState<BalloonGameRun | null>(null);

  const phaseRef = useRef<LocalPhase>('ready');
  const stateRef = useRef<BalloonState>(initialState(mode));
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

  const endingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 결과를 확인(계속)해서 onRoundEnd를 이미 불렀는지. 연결 약속상 라운드당 한 번만 부른다 */
  const confirmedRef = useRef(false);

  const changePhase = (next: LocalPhase) => {
    phaseRef.current = next;
    setRoundPhase(next);
  };

  const clearEndingTimer = () => {
    if (endingTimerRef.current) clearTimeout(endingTimerRef.current);
    endingTimerRef.current = null;
  };

  // 화면이 사라지면 결과 화면으로 넘어가려던 예약을 취소한다
  useEffect(() => clearEndingTimer, []);

  function finishRound() {
    if (finishedRef.current) return;
    finishedRef.current = true;
    // 마지막 모습을 잠깐 보여 준 뒤 결과 요약으로 넘어간다
    changePhase('ending');
    clearEndingTimer();
    endingTimerRef.current = setTimeout(() => changePhase('finished'), ENDING_MS);
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

    stateRef.current = stepBalloon(stateRef.current, diff, dt, getLevel(levelRef.current, mptRef.current), voiced, mode);
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
    if (phaseRef.current !== 'ready') return; // 시작 단추를 연달아 눌러도 한 번만
    confirmedRef.current = false;
    stateRef.current = initialState(mode);
    diffsRef.current = [];
    finishedRef.current = false;
    startedAtRef.current = new Date().toISOString();
    setState(stateRef.current);
    setDbDiff(0);
    setLastRun(null);
    changePhase('playing');
    await input.start();
  };

  const confirm = () => {
    if (!lastRun || confirmedRef.current) return; // 계속 단추를 연달아 눌러도 한 번만
    confirmedRef.current = true;
    onRoundEndRef.current(lastRun);
  };

  const exit = async () => {
    finishedRef.current = true;
    clearEndingTimer();
    await input.stop();
    onExitRef.current();
  };

  return { phase, state, dbDiff, target: getLevel(level, baselines.maxPhonationTimeS), lastRun, error: input.error, start, confirm, exit };
}
