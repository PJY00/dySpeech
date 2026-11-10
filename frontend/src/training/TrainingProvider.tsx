// 시안 화면(src/app)이 함께 쓰는 훈련 상태. AppShell.tsx가 화면 하나에서 들고 있던 것을 여러 화면이 나눠 쓰게 옮겼다.
// - 회차(세션)와 측정 블록 기준값, 게임별 라운드 번호와 단계, 마지막 라운드 결과
// - 오늘 훈련 현황(N/3, progress.ts 규칙), 최근 회차 카드, 끝나지 않은 회차
// 회차는 하루 하나: 오늘 시작한 회차가 열려 있으면 이어서 하고, 없으면 새 회차를 열고 측정 블록부터 한다.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
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
  const sessionRef = useRef<Session | null>(null);
  const levels = useMemo(() => new LevelBook(stateStore), []);
  const today = useMemo(() => new TodayBook(stateStore), []);

  const setCurrent = useCallback((s: Session | null) => {
    sessionRef.current = s;
    setSession(s);
  }, []);

  const refreshCards = useCallback(async () => {
    setCards(recentCards(await listSessions(), RECENT_CARDS));
  }, []);

  // 앱 시작: 사용자, 못 보낸 회차 이어 보내기, 끝나지 않은 회차, 오늘 기록, 최근 회차
  useEffect(() => {
    void (async () => {
      try {
        const user = await currentUser();
        setUserKey(user.userId);
        const r = await resumePending();
        log('resume', { resumed: r.resumed, unfinished: r.unfinished.length, swept_files: r.sweptFiles });
        setUnfinished(r.unfinished);
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
  }, [refreshCards, today]);

  const openNewSession = useCallback(
    async (purpose: 'training' | 'retest') => {
      const s = await startSession(purpose);
      log('session_started', { session_id: s.id, purpose });
      setCurrent(s);
      setBaselines(null);
      setRounds({});
      setLastRound(null);
      return s;
    },
    [setCurrent],
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
      // 측정 블록을 끝내지 못한 회차나 지난 날 회차는 버리고 새로 연다
      if (open && !open.baselines()) await open.discard().catch(() => undefined);
      await openNewSession('training');
      router.push({ pathname: '/session/measure', params: { next: destination } });
    },
    [openNewSession],
  );

  const startRetest = useCallback(async () => {
    const open = sessionRef.current;
    if (open && !open.baselines()) await open.discard().catch(() => undefined);
    await openNewSession('retest');
    router.push({ pathname: '/session/measure', params: { next: '/session/finishing' } });
  }, [openNewSession]);

  const onMeasured = useCallback((b: Baselines) => setBaselines(gameBaselines(b)), []);

  const cancelMeasure = useCallback(async () => {
    const s = sessionRef.current;
    setCurrent(null);
    setBaselines(null);
    await s?.discard().catch(() => undefined);
  }, [setCurrent]);

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
      setRounds((r) => ({ ...r, [entry.game]: (r[entry.game] ?? 0) + 1 }));
      setTodayRounds(
        await today.add({ game: entry.game, area: entry.area, success_rate_pct: run.success_rate_pct, decision: run.decision }),
      );
      const nextLevel = await levels.record(userKey, entry.game, run.level, run.decision, entry.maxLevel);
      const last = { entry, run, nextLevel };
      setLastRound(last);
      return last;
    },
    [levels, today, userKey],
  );

  const finish = useCallback(async () => {
    const s = sessionRef.current;
    if (!s) return null;
    await s.complete();
    log('session_completed', { session_id: s.id });
    setCurrent(null);
    setBaselines(null);
    setRounds({});
    void refreshCards();
    return s;
  }, [refreshCards, setCurrent]);

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
    [ready, userKey, session, baselines, lastRound, rounds, todayRounds, cards, unfinished, startTraining, startRetest, onMeasured, cancelMeasure, levelFor, nextRoundIndex, recordRound, finish, refreshCards, resolveUnfinished, resetToday],
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
