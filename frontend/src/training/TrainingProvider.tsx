// 시안 화면(src/app)이 함께 쓰는 훈련 상태. AppShell.tsx가 화면 하나에서 들고 있던 것을 여러 화면이 나눠 쓰게 옮겼다.
// - 회차(세션)와 측정 블록 기준값, 게임별 라운드 번호와 단계, 마지막 라운드 결과
// - 오늘 훈련 현황(N/3, progress.ts 규칙), 최근 회차 카드, 끝나지 않은 회차
// 회차는 하루 하나(openSession.ts): 오늘 측정을 마친 회차는 앱을 다시 열어도 이어서 하고, [오늘 훈련 끝] 뒤 그날은 새 회차를 열지 않는다.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert } from 'react-native';
import { router, type Href } from 'expo-router';

import type { GameEntry } from '../games/registry';
import { isTempGame } from '../games/temp';
import {
  currentUser,
  discardRecordingFile,
  listSessions,
  resumePending,
  startAutoSync,
  startSession,
  stateStore,
} from '../session';
import type { Baselines, GameRun, Session } from '../session';
import { gameBaselines } from './gameTypes';
import type { GameArea, GameBaselines, RoundRecording } from './gameTypes';
import { recentCards } from './labels';
import type { SessionCard } from './labels';
import { LevelBook } from './levels';
import { OpenSessionBook, planUnfinished, reportedToday } from './openSession';
import { completedAreas, dayKey, TodayBook } from './progress';
import type { RoundSummary } from './progress';

const RECENT_CARDS = 3;

const log = (event: string, data?: unknown) => console.log('[app]', event, data === undefined ? '' : JSON.stringify(data));

export interface LastRound {
  entry: GameEntry;
  run: GameRun;
  nextLevel: number;
}

interface Training {
  ready: boolean;
  userKey: string;
  /** 지금 열려 있는 회차(complete 전) */
  session: Session | null;
  baselines: GameBaselines | null;
  lastRound: LastRound | null;
  /** 이번 회차에서 끝낸 라운드 수 */
  roundsDone: number;
  /** 오늘 완료한 영역(N/3) */
  completed: GameArea[];
  cards: SessionCard[];
  unfinished: Session[];
  /** 오늘 훈련 회차를 이미 끝냈는지([오늘 훈련 끝] 뒤 그날은 새 회차를 열지 않음) */
  trainedToday: boolean;
  /** 훈련으로 들어간다. 측정 블록을 마친 오늘 회차가 있으면 바로, 없으면 새 회차를 열고 측정 블록부터 */
  startTraining: (destination: string) => Promise<void>;
  /** 반복 측정(retest): 측정 블록만 하고 바로 보낸다 */
  startRetest: () => Promise<void>;
  onMeasured: (baselines: Baselines) => void;
  cancelMeasure: () => Promise<void>;
  levelFor: (game: string) => Promise<number>;
  nextRoundIndex: (game: string) => number;
  recordRound: (entry: GameEntry, run: GameRun, recording?: RoundRecording) => Promise<LastRound>;
  /** 회차를 끝내고(complete) 보내기를 시작한다. 끝낸 회차를 돌려준다 */
  finish: () => Promise<Session | null>;
  refreshCards: () => Promise<void>;
  resolveUnfinished: (s: Session, action: 'send' | 'discard') => Promise<void>;
  resetToday: () => Promise<void>;
}

const TrainingContext = createContext<Training | null>(null);

export function TrainingProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [userKey, setUserKey] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [baselines, setBaselines] = useState<GameBaselines | null>(null);
  const [lastRound, setLastRound] = useState<LastRound | null>(null);
  const [rounds, setRounds] = useState<Record<string, number>>({});
  const [todayRounds, setTodayRounds] = useState<RoundSummary[]>([]);
  const [cards, setCards] = useState<SessionCard[]>([]);
  const [unfinished, setUnfinished] = useState<Session[]>([]);
  const [trainedToday, setTrainedToday] = useState(false);
  const sessionRef = useRef<Session | null>(null);
  const roundsRef = useRef<Record<string, number>>({});
  const levels = useMemo(() => new LevelBook(stateStore), []);
  const today = useMemo(() => new TodayBook(stateStore), []);
  const openBook = useMemo(() => new OpenSessionBook(stateStore), []);

  const setCurrent = useCallback((s: Session | null) => {
    sessionRef.current = s;
    setSession(s);
  }, []);

  const setRoundCounts = useCallback((next: Record<string, number>) => {
    roundsRef.current = next;
    setRounds(next);
  }, []);

  const refreshCards = useCallback(async () => {
    const list = await listSessions();
    setCards(recentCards(list, RECENT_CARDS));
    setTrainedToday(reportedToday(list));
  }, []);

  // 앱 시작: 사용자, 못 보낸 회차 이어 보내기, 끝나지 않은 회차, 오늘 기록, 최근 회차
  useEffect(() => {
    void (async () => {
      try {
        const user = await currentUser();
        setUserKey(user.userId);
        const r = await resumePending();
        log('resume', { resumed: r.resumed, unfinished: r.unfinished.length, swept_files: r.sweptFiles });
        // 오늘 측정을 마친 회차는 이어서, 지난 날 회차는 마지막 라운드 시각으로 끝내고, 나머지만 묻는다
        const doc = await openBook.read();
        const plan = planUnfinished(r.unfinished, doc);
        for (const { session: old, endedAt } of plan.close) {
          await old.complete({ endedAt });
          log('session_auto_completed', { session_id: old.id, ended_at: endedAt });
        }
        const reopen = plan.reopen;
        const measured = reopen?.baselines();
        if (reopen && measured) {
          setCurrent(reopen);
          setBaselines(gameBaselines(measured));
          setRoundCounts(doc?.sessionId === reopen.id ? doc.rounds : {});
          log('session_reopened', { session_id: reopen.id });
        }
        setUnfinished(plan.ask);
        setTodayRounds(await today.rounds());
        await refreshCards();
        // resumePending은 보내기를 뒤에서 시작한다. 시작된 뒤 카드를 다시 그린다
        setTimeout(() => void refreshCards(), 1000);
      } catch (e) {
        log('start_error', { message: String(e) });
      } finally {
        setReady(true);
      }
    })();
    return startAutoSync(() => void refreshCards());
  }, [refreshCards, today, openBook, setCurrent, setRoundCounts]);

  const openNewSession = useCallback(
    async (purpose: 'training' | 'retest') => {
      const s = await startSession(purpose);
      log('session_started', { session_id: s.id, purpose });
      setCurrent(s);
      setBaselines(null);
      setRoundCounts({});
      setLastRound(null);
      return s;
    },
    [setCurrent, setRoundCounts],
  );

  const startTraining = useCallback(
    async (destination: string) => {
      const open = sessionRef.current;
      const usable =
        open && open.purpose === 'training' && open.baselines() && dayKey(new Date(open.startedAt)) === dayKey();
      if (usable) {
        router.push(destination as Href);
        return;
      }
      if (open && open.purpose === 'training' && open.baselines()) {
        // 앱을 켠 채로 날짜가 바뀐 회차: 마지막 라운드 시각으로 끝낸다
        const doc = await openBook.read();
        await open.complete({ endedAt: doc?.sessionId === open.id ? doc.lastActivityAt : open.baselines()?.measuredAt });
        setCurrent(null);
        await openBook.write(null);
      } else if (open && !open.baselines()) {
        // 측정 블록을 끝내지 못한 회차는 버린다
        await open.discard().catch(() => undefined);
        setCurrent(null);
      }
      if (reportedToday(await listSessions())) {
        setTrainedToday(true);
        Alert.alert('오늘 훈련을 마쳤어요', '측정은 하루에 한 번만 해요. 내일 다시 만나요!');
        return;
      }
      await openNewSession('training');
      router.push({ pathname: '/session/measure', params: { next: destination } });
    },
    [openNewSession, openBook, setCurrent],
  );

  const startRetest = useCallback(async () => {
    const open = sessionRef.current;
    if (open && !open.baselines()) await open.discard().catch(() => undefined);
    await openNewSession('retest');
    router.push({ pathname: '/session/measure', params: { next: '/session/finishing' } });
  }, [openNewSession]);

  const onMeasured = useCallback(
    (b: Baselines) => {
      setBaselines(gameBaselines(b));
      const s = sessionRef.current;
      if (s?.purpose === 'training') void openBook.write({ sessionId: s.id, rounds: {}, lastActivityAt: b.measuredAt });
    },
    [openBook],
  );

  const cancelMeasure = useCallback(async () => {
    const s = sessionRef.current;
    setCurrent(null);
    setBaselines(null);
    await s?.discard().catch(() => undefined);
    await openBook.write(null);
  }, [setCurrent, openBook]);

  const levelFor = useCallback((game: string) => levels.get(userKey, game), [levels, userKey]);

  const nextRoundIndex = useCallback((game: string) => (rounds[game] ?? 0) + 1, [rounds]);

  const recordRound = useCallback(
    async (entry: GameEntry, run: GameRun, recording?: RoundRecording) => {
      log('round_end', { game: run.game, round_index: run.round_index, level: run.level, success_rate_pct: run.success_rate_pct, decision: run.decision });
      // 임시 게임 라운드는 서버 회차에 넣지 않는다(오늘 현황·단계 기록에만 반영)
      const s = isTempGame(entry) ? null : sessionRef.current;
      if (s) {
        try {
          await s.addGameRun(run);
          if (recording) await s.addRecording({ kind: 'game', gameRunId: run.game_run_id, fileUri: recording.fileUri });
        } catch (e) {
          log('round_save_error', { message: String(e) });
          if (recording) await discardRecordingFile(recording.fileUri).catch(() => undefined);
        }
      } else if (recording) {
        await discardRecordingFile(recording.fileUri).catch(() => undefined);
      }
      const counts = { ...roundsRef.current, [entry.game]: (roundsRef.current[entry.game] ?? 0) + 1 };
      setRoundCounts(counts);
      const open = sessionRef.current;
      if (open?.purpose === 'training') {
        await openBook.write({ sessionId: open.id, rounds: counts, lastActivityAt: new Date().toISOString() });
      }
      setTodayRounds(
        await today.add({ game: entry.game, area: entry.area, success_rate_pct: run.success_rate_pct, decision: run.decision }),
      );
      const nextLevel = await levels.record(userKey, entry.game, run.level, run.decision, entry.maxLevel);
      const last = { entry, run, nextLevel };
      setLastRound(last);
      return last;
    },
    [levels, today, userKey, openBook, setRoundCounts],
  );

  const finish = useCallback(async () => {
    const s = sessionRef.current;
    if (!s) return null;
    await s.complete();
    log('session_completed', { session_id: s.id });
    setCurrent(null);
    setBaselines(null);
    setRoundCounts({});
    if (s.purpose === 'training') await openBook.write(null);
    void refreshCards();
    return s;
  }, [refreshCards, setCurrent, setRoundCounts, openBook]);

  const resolveUnfinished = useCallback(
    async (s: Session, action: 'send' | 'discard') => {
      if (action === 'send') await s.complete();
      else await s.discard();
      setUnfinished((list) => list.filter((x) => x.id !== s.id));
      await refreshCards();
    },
    [refreshCards],
  );

  const resetToday = useCallback(async () => setTodayRounds(await today.reset()), [today]);

  const value = useMemo<Training>(
    () => ({
      ready,
      userKey,
      session,
      baselines,
      lastRound,
      roundsDone: Object.values(rounds).reduce((a, b) => a + b, 0),
      completed: completedAreas(todayRounds),
      cards,
      unfinished,
      trainedToday,
      startTraining,
      startRetest,
      onMeasured,
      cancelMeasure,
      levelFor,
      nextRoundIndex,
      recordRound,
      finish,
      refreshCards,
      resolveUnfinished,
      resetToday,
    }),
    [ready, userKey, session, baselines, lastRound, rounds, todayRounds, cards, unfinished, trainedToday, startTraining, startRetest, onMeasured, cancelMeasure, levelFor, nextRoundIndex, recordRound, finish, refreshCards, resolveUnfinished, resetToday],
  );

  return <TrainingContext.Provider value={value}>{children}</TrainingContext.Provider>;
}

export function useTraining(): Training {
  const context = useContext(TrainingContext);
  if (!context) {
    throw new Error('useTraining must be used inside TrainingProvider');
  }
  return context;
}
