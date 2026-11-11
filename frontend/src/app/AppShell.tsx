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
  openSession,
  refreshStatuses,
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
import { cardStatus, needsPolling, recentCards } from './labels';
import type { SessionCard } from './labels';
import { LevelBook } from './levels';
import { ChooseScreen, FinishingScreen, HomeScreen, RecordsScreen, ResultScreen, ResumeScreen, RoundEndScreen } from './screens';

/** 결과 확인 간격(3~5초 중 4초). 메인의 최근 회차 갱신도 같은 간격 */
const RESULT_POLL_MS = 4000;
/** 메인에 보여 줄 최근 회차 수 */
const RECENT_CARDS = 3;

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
  const [cards, setCards] = useState<SessionCard[]>([]);
  const [olderUnsent, setOlderUnsent] = useState(0);
  const [devRuns, setDevRuns] = useState<GameRun[]>([]);
  const session = useRef<Session | null>(null);
  const finishingStarted = useRef(false);
  /** 지금 화면의 결과 기다리기(4초 상태 확인). 화면을 떠나면 멈춘다. 보내기는 세션 모듈이 계속한다 */
  const waitAbort = useRef<AbortController | null>(null);
  const homePolling = useRef(false);
  const levels = useMemo(() => new LevelBook(stateStore), []);

  /** 메인의 최근 회차 카드를 기기 상태로 다시 그린다 */
  const refreshCards = useCallback(async () => {
    const list = await listSessions();
    const shown = recentCards(list, RECENT_CARDS);
    const shownIds = new Set(shown.map((c) => c.summary.sessionId));
    setCards(shown);
    setOlderUnsent(list.filter((x) => !shownIds.has(x.sessionId) && cardStatus(x) === 'unsent').length);
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
        await refreshCards();
        // resumePending은 보내기를 뒤에서 시작한다. 시작된 뒤(보내는 중) 카드로 다시 그린다
        setTimeout(() => void refreshCards(), 1000);
      } catch (e) {
        setNotice(`시작 처리 중 오류: ${String(e)}`);
      }
    })();
    return startAutoSync(() => void refreshCards());
  }, [dev, refreshCards]);

  // 화면이 사라지면 결과 기다리기를 멈춘다
  useEffect(() => () => waitAbort.current?.abort(), []);

  // 메인에 있는 동안: 보내는 중·계산 중인 카드가 있으면 4초마다 갱신, 없으면 멈춤
  const pollHome = flow.screen.name === 'home' && !dev && needsPolling(cards);
  useEffect(() => {
    if (!pollHome) return;
    const timer = setInterval(() => {
      if (homePolling.current) return;
      homePolling.current = true;
      void (async () => {
        try {
          await refreshStatuses();
        } catch (e) {
          log('refresh_error', { message: String(e) });
        }
        await refreshCards();
        homePolling.current = false;
      })();
    }, RESULT_POLL_MS);
    return () => clearInterval(timer);
  }, [pollHome, refreshCards]);

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

  /** 결과 화면의 결과 기다리기. 앞의 기다리기는 멈추고 새로 시작한다 */
  async function waitResult(s: Session) {
    waitAbort.current?.abort();
    const ac = new AbortController();
    waitAbort.current = ac;
    setChecking(true);
    try {
      const p = await s.waitForResult({
        intervalMs: RESULT_POLL_MS,
        signal: ac.signal,
        onProgress: (x) => {
          if (!ac.signal.aborted) setProgress(x);
        },
      });
      if (!ac.signal.aborted) setProgress(p);
    } finally {
      if (waitAbort.current === ac) {
        waitAbort.current = null;
        setChecking(false);
      }
    }
  }

  /** 결과 기다리기만 멈춘다(보내기는 계속) */
  function stopWaiting() {
    waitAbort.current?.abort();
    waitAbort.current = null;
    setChecking(false);
  }

  /** 메인의 카드를 눌러 그 회차 결과 화면을 연다. 보내는 중·계산 중이면 결과를 기다린다 */
  async function openResult(sessionId: string) {
    const s = await openSession(sessionId);
    if (!s) {
      await refreshCards();
      return;
    }
    session.current = s;
    const p = s.status();
    setProgress(p);
    dispatch({ type: 'openResult' });
    const status = cardStatus({ state: p.state, lastErrorCode: p.lastError?.code ?? null });
    if (status === 'sending' || status === 'processing') void waitResult(s);
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
      // 저장하는 동안 메인으로 가면 결과 기다리기를 시작하지 않는다(보내기는 complete가 시작해 계속 돈다)
      const left = new AbortController();
      waitAbort.current = left;
      await s.complete();
      if (left.signal.aborted) return;
      setProgress(s.status());
      dispatch({ type: 'finished' });
      await waitResult(s);
    })();
  }, [flow.screen.name, dev, devRuns]);

  /** 메인으로: 이 화면의 결과 기다리기만 멈추고, 보내기·서버 계산은 그대로 둔다 */
  function goHome() {
    stopWaiting();
    session.current = null;
    setProgress(null);
    setDevRuns([]);
    finishingStarted.current = false;
    dispatch({ type: 'home' });
    void refreshCards();
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
          userKey={userKey}
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
      return <FinishingScreen progress={progress} onHome={goHome} />;
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
          cards={cards}
          olderUnsent={olderUnsent}
          onTraining={() => void start('training')}
          onRetest={() => void start('retest')}
          onRecords={() => dispatch({ type: 'records' })}
          onOpen={(id) => void openResult(id)}
        />
      );
  }
}
