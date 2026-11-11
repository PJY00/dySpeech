// 앱 기본 화면 구조: 메인 → 오늘 훈련(측정 블록 → 게임 선택 → 라운드 반복 → 끝 → 결과) / 반복 측정(측정 블록 → 결과) / 기록(자리만).
// 화면 전환은 새 라이브러리 없이 상태값(flow.ts)으로 한다.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';

import { findGame } from '../games/registry';
import type { GameEntry, GameName } from '../games/registry';
import { MeasurementBlock } from '../measurement';
import {
  currentUser,
  discardRecordingFile,
  listSessions,
  resumePending,
  startAutoSync,
  startSession,
  stateStore,
} from '../session';
import type { Baselines, GameRun, Session, SessionProgress } from '../session';
import { TEMP_BASELINES } from './devScreen';
import { initialFlow, reduceFlow, totalRounds, unfinishedOptions } from './flow';
import type { FlowState } from './flow';
import { gameBaselines } from './gameTypes';
import type { GameBaselines, RoundRecording } from './gameTypes';
import { LevelBook } from './levels';
import { ChooseScreen, FinishingScreen, HomeScreen, RecordsScreen, ResultScreen, ResumeScreen, RoundEndScreen } from './screens';

/** 결과 확인 간격(3~5초 중 4초) */
const RESULT_POLL_MS = 4000;

export type DevStart = { start: 'choose' } | { start: 'game'; game: GameName };

const log = (event: string, data?: unknown) => console.log('[app]', event, data === undefined ? '' : JSON.stringify(data));

function firstState(dev: DevStart | undefined): FlowState {
  if (!dev) return initialFlow();
  if (dev.start === 'game' && findGame(dev.game)) {
    return initialFlow({ name: 'game', game: dev.game, level: 1, roundIndex: 1 }, 'training');
  }
  return initialFlow({ name: 'choose' }, 'training');
}

export function AppShell({ dev, notice: initialNotice }: { dev?: DevStart; notice?: string }) {
  const [flow, dispatch] = useReducer(reduceFlow, dev, firstState);
  const [notice, setNotice] = useState<string | null>(
    initialNotice ?? (dev?.start === 'game' && !findGame(dev.game) ? `${dev.game} 게임이 아직 등록되지 않아 게임 선택 화면을 열었어요` : null),
  );
  const [baselines, setBaselines] = useState<GameBaselines | null>(dev ? TEMP_BASELINES : null);
  const [userKey, setUserKey] = useState<string>(dev ? 'dev' : '');
  const [unfinished, setUnfinished] = useState<Session[]>([]);
  const [progress, setProgress] = useState<SessionProgress | null>(null);
  const [checking, setChecking] = useState(false);
  const [waiting, setWaiting] = useState(0);
  const [devRuns, setDevRuns] = useState<GameRun[]>([]);
  const session = useRef<Session | null>(null);
  const finishingStarted = useRef(false);
  const levels = useMemo(() => new LevelBook(stateStore), []);

  const refreshWaiting = useCallback(async () => {
    const list = await listSessions();
    setWaiting(list.filter((s) => s.state === 'queued' || s.state === 'sending' || s.state === 'blocked').length);
  }, []);

  // 앱 시작: 못 보낸 세션 이어서 보내기, 끝나지 않은 회차 묻기, 남은 녹음 파일 정리
  useEffect(() => {
    if (dev) return;
    void (async () => {
      try {
        const user = await currentUser();
        setUserKey(user.userId);
        const r = await resumePending();
        log('resume', { resumed: r.resumed, unfinished: r.unfinished.length, swept_files: r.sweptFiles });
        if (r.unfinished.length) {
          setUnfinished(r.unfinished);
          dispatch({ type: 'unfinished' });
        }
        await refreshWaiting();
      } catch (e) {
        setNotice(`시작 처리 중 오류: ${String(e)}`);
      }
    })();
    return startAutoSync();
  }, [dev, refreshWaiting]);

  async function start(purpose: 'training' | 'retest') {
    setNotice(null);
    setProgress(null);
    finishingStarted.current = false;
    session.current = await startSession(purpose);
    log('session_started', { session_id: session.current.id, purpose });
    dispatch({ type: 'start', purpose });
  }

  function onMeasured(b: Baselines) {
    setBaselines(gameBaselines(b));
    dispatch({ type: 'measured' });
  }

  async function pick(entry: GameEntry) {
    const level = await levels.get(userKey, entry.game);
    dispatch({ type: 'pickGame', game: entry.game, level });
  }

  async function onRoundEnd(entry: GameEntry, run: GameRun, recording?: RoundRecording) {
    log('round_end', { game: run.game, round_index: run.round_index, level: run.level, success_rate_pct: run.success_rate_pct, decision: run.decision });
    if (dev) {
      setDevRuns((runs) => [...runs, run]);
      if (recording) await discardRecordingFile(recording.fileUri);
    } else if (session.current) {
      try {
        await session.current.addGameRun(run);
        if (recording) await session.current.addRecording({ kind: 'game', gameRunId: run.game_run_id, fileUri: recording.fileUri });
      } catch (e) {
        log('round_save_error', { message: String(e) });
        setNotice(`라운드 기록 저장 중 오류: ${e instanceof Error ? e.message : String(e)}`);
        if (recording) await discardRecordingFile(recording.fileUri).catch(() => undefined);
      }
    }
    const nextLevel = await levels.record(userKey, entry.game, run.level, run.decision, entry.maxLevel);
    dispatch({ type: 'roundEnded', run, nextLevel });
  }

  async function waitResult(s: Session) {
    setChecking(true);
    try {
      setProgress(await s.waitForResult({ intervalMs: RESULT_POLL_MS, onProgress: setProgress }));
    } finally {
      setChecking(false);
    }
  }

  // 오늘 훈련 끝·반복 측정 끝: complete → 결과(done·failed까지 4초 간격 확인)
  useEffect(() => {
    if (flow.screen.name !== 'finishing' || finishingStarted.current) return;
    finishingStarted.current = true;
    void (async () => {
      if (dev) {
        log('dev_runs', devRuns);
        dispatch({ type: 'finished' });
        return;
      }
      const s = session.current;
      if (!s) {
        dispatch({ type: 'finished' });
        return;
      }
      await s.complete();
      setProgress(s.status());
      dispatch({ type: 'finished' });
      await waitResult(s);
      await refreshWaiting();
    })();
  }, [flow.screen.name, dev, devRuns, refreshWaiting]);

  function goHome() {
    session.current = null;
    setProgress(null);
    setDevRuns([]);
    finishingStarted.current = false;
    dispatch({ type: 'home' });
    void refreshWaiting();
  }

  const screen = flow.screen;
  switch (screen.name) {
    case 'resume':
      return (
        <ResumeScreen
          sessions={unfinished}
          onSend={(s) => {
            if (!unfinishedOptions(s).canSend) return;
            void s.complete().then(() => setUnfinished((list) => list.filter((x) => x.id !== s.id)));
          }}
          onDiscard={(s) => void s.discard().then(() => setUnfinished((list) => list.filter((x) => x.id !== s.id)))}
          onDone={goHome}
        />
      );
    case 'measure':
      return session.current ? (
        <MeasurementBlock
          session={session.current}
          onDone={onMeasured}
          onCancel={() => {
            const s = session.current;
            session.current = null;
            void s?.discard();
            dispatch({ type: 'cancel' });
          }}
        />
      ) : null;
    case 'choose':
      return <ChooseScreen dev={Boolean(dev)} roundsDone={totalRounds(flow)} onPick={(g) => void pick(g)} onFinish={() => dispatch({ type: 'finish' })} />;
    case 'game': {
      const entry = findGame(screen.game);
      if (!entry || !baselines) return null;
      const Game = entry.component;
      return (
        <Game
          key={`${screen.game}-${screen.roundIndex}`}
          baselines={baselines}
          level={screen.level}
          roundIndex={screen.roundIndex}
          onRoundEnd={(run, recording) => void onRoundEnd(entry, run, recording)}
          onExit={() => dispatch({ type: 'chooseOther' })}
        />
      );
    }
    case 'roundEnd':
      return (
        <RoundEndScreen
          game={findGame(screen.game)}
          run={screen.run}
          nextLevel={screen.nextLevel}
          onNext={() => dispatch({ type: 'nextRound' })}
          onOther={() => dispatch({ type: 'chooseOther' })}
          onFinish={() => dispatch({ type: 'finish' })}
        />
      );
    case 'finishing':
      return <FinishingScreen progress={progress} />;
    case 'result':
      return (
        <ResultScreen
          progress={progress}
          devRuns={dev ? devRuns : null}
          checking={checking}
          onCheck={() => session.current && void waitResult(session.current)}
          onHome={goHome}
        />
      );
    case 'records':
      return <RecordsScreen onHome={goHome} />;
    case 'home':
      return (
        <HomeScreen
          notice={notice}
          waiting={waiting}
          onTraining={() => void start('training')}
          onRetest={() => void start('retest')}
          onRecords={() => dispatch({ type: 'records' })}
        />
      );
  }
}
