// 노래방 따라 읽기(game = guide_reading, 말속도 영역). 라운드 하나 = 문장 5개.
// 안내 커서가 목표 속도로 글자 위를 지나가고, 사용자가 읽은 곳은 아래 점으로 따로 보여 준다.
// 문장을 끝내는 조건은 endRule.ts(80% 넘게 읽었으면 1초 무음, 아니면 안내가 끝난 뒤 3초 무음). 화면은 React Native 기본 애니메이션(Animated)만 쓴다.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ActivityIndicator, Animated, Easing, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { GameProps } from '../../app/gameTypes';
import { MeasurementRecorder, prepareMicrophone } from '../../measurement/recorder';
import { discardRecordingFile, getHistory, readRecordingFile, stateStore, uuidv4 } from '../../session';
import type { History } from '../../session';
import { decodePcm16, parseWav } from '../../session/wav';
import { analyzeSentence } from './analysis';
import { fillsProgress, SentenceEnd } from './endRule';
import type { EndReason } from './endRule';
import { LiveReader } from './live';
import { loadReadingState, recordDetection } from './reference';
import type { ReadingState } from './reference';
import { buildGameRun, judgeSentence, REASON_TEXT, targetFor } from './scoring';
import type { SentenceResult } from './scoring';
import { roundSentences } from './sentences';
import type { TrainingSentence } from './sentences';

/** 녹음을 시작하고 안내 커서가 움직이기 전까지(초). 이 동안 잡음 바닥을 잰다 */
const PREROLL_S = 1;
/** 진전도를 기다리는 최대 시간(밀리초). 넘으면 저장된 기준으로 시작 */
const HISTORY_TIMEOUT_MS = 3000;

type Phase = 'loading' | 'denied' | 'ready' | 'reading' | 'analyzing' | 'feedback' | 'error';

const log = (event: string, data?: unknown) => console.log('[guide_reading]', event, data === undefined ? '' : JSON.stringify(data));
const isHangul = (ch: string) => {
  const c = ch.codePointAt(0) ?? 0;
  return c >= 0xac00 && c <= 0xd7a3;
};

async function historyOrNull(): Promise<History | null> {
  try {
    return await Promise.race([getHistory(), new Promise<null>((r) => setTimeout(() => r(null), HISTORY_TIMEOUT_MS))]);
  } catch {
    return null;
  }
}

export function GuideReadingGame({ baselines, level, roundIndex, userKey, onRoundEnd, onExit }: GameProps) {
  const sentences = useMemo(() => roundSentences(roundIndex), [roundIndex]);
  const [phase, setPhase] = useState<Phase>('loading');
  const [index, setIndex] = useState(0);
  const [reading, setReading] = useState<ReadingState | null>(null);
  const [results, setResults] = useState<SentenceResult[]>([]);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(new Animated.Value(0)).current;
  const recorder = useRef<MeasurementRecorder | null>(null);
  const stopping = useRef(false);
  const startedAt = useRef(new Date().toISOString());
  const gameRunId = useRef(uuidv4());
  const guideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sentence = sentences[index];
  const target = reading ? targetFor(reading.referenceRate, level) : null;

  useEffect(() => {
    void (async () => {
      try {
        if ((await prepareMicrophone()) !== 'granted') {
          setPhase('denied');
          return;
        }
        const state = await loadReadingState(stateStore, userKey, baselines.articulationRateSps, await historyOrNull());
        log('reference', { user_key: userKey, ...state, level });
        setReading(state);
        recorder.current = new MeasurementRecorder();
        setPhase('ready');
      } catch (e) {
        fail(e);
      }
    })();
    return () => {
      if (guideTimer.current) clearTimeout(guideTimer.current);
      void recorder.current?.release().then((left) => (left ? discardRecordingFile(left.fileUri) : undefined));
    };
  }, []);

  function fail(e: unknown) {
    const message = e instanceof Error ? e.message : String(e);
    log('error', { message });
    setError(message);
    setPhase('error');
  }

  async function start() {
    if (!recorder.current || !reading || !target) return;
    const s = sentence;
    const guideS = s.syllables / target.guideRate;
    const live = new LiveReader({
      sampleRate: 48000,
      syllables: s.syllables,
      referenceRate: target.guideRate,
      detectionRatio: reading.detectionRatio,
      noiseFloorDbfs: baselines.noiseFloorDbfs,
      searchRange: baselines.f0SearchRangeHz,
    });
    const end = new SentenceEnd({
      syllables: s.syllables,
      floorDbfs: baselines.noiseFloorDbfs,
      guideStartS: PREROLL_S,
      guideS,
      maxS: PREROLL_S + guideS * 2.5 + 4,
    });
    stopping.current = false;
    cursor.setValue(0);
    setProgress(0);
    setPhase('reading');
    let n = 0;
    try {
      await recorder.current.start(uuidv4(), (lvl, dt, raw) => {
        live.push(raw);
        n += 1;
        if (n % 5 === 0) setProgress(live.estimate);
        if (end.push(lvl, dt, live.estimate)) {
          if (!stopping.current) log('sentence_end', { reason: end.reason, elapsed_s: end.elapsedS, estimate: live.estimate });
          void stop(live, end.reason ?? 'max');
        }
      });
    } catch (e) {
      fail(e);
      return;
    }
    guideTimer.current = setTimeout(() => {
      Animated.timing(cursor, { toValue: s.syllables, duration: guideS * 1000, easing: Easing.linear, useNativeDriver: false }).start();
    }, PREROLL_S * 1000);
    log('sentence_start', { sentence_id: s.id, guide_rate: target.guideRate, guide_s: guideS });
  }

  async function stop(live: LiveReader, endReason: EndReason) {
    if (stopping.current || !recorder.current || !target) return;
    stopping.current = true;
    if (guideTimer.current) clearTimeout(guideTimer.current);
    cursor.stopAnimation();
    // 80% 넘게 읽고 끝났을 때만 진행 표시를 끝까지 채운다
    if (fillsProgress(endReason)) live.finish();
    setProgress(live.estimate);
    setPhase('analyzing');
    try {
      const file = await recorder.current.stop();
      await new Promise((r) => setTimeout(r, 30));
      const bytes = await readRecordingFile(file.fileUri);
      const info = parseWav(bytes);
      const a = analyzeSentence(decodePcm16(bytes, info), info.sampleRate, sentence.syllables, {
        noiseFloorDbfs: baselines.noiseFloorDbfs,
        searchRange: baselines.f0SearchRangeHz,
      });
      const r = judgeSentence(sentence.id, sentence.syllables, a, target, endReason);
      const ratio = await recordDetection(stateStore, userKey, a.detected, sentence.syllables);
      setReading((st) => (st ? { ...st, detectionRatio: ratio } : st));
      await discardRecordingFile(file.fileUri);
      log('sentence_done', { ...r, live_detected: live.detected, detection_ratio: ratio });
      setResults((list) => [...list, r]);
      setPhase('feedback');
    } catch (e) {
      fail(e);
    }
  }

  /** 준비 화면으로 돌아갈 때 안내 커서와 진행 표시를 0으로 */
  function resetGuide() {
    cursor.stopAnimation();
    cursor.setValue(0);
    setProgress(0);
  }

  /** 같은 문장을 다시 읽는다. 끝까지 읽지 못한 앞 시도는 결과에 남지만 판정에 세지 않는다 */
  function retry() {
    resetGuide();
    setPhase('ready');
  }

  function next() {
    resetGuide();
    if (index + 1 < sentences.length) {
      setIndex(index + 1);
      setPhase('ready');
      return;
    }
    if (!reading || !target) return;
    const run = buildGameRun(
      {
        gameRunId: gameRunId.current,
        level,
        roundIndex,
        startedAt: startedAt.current,
        referenceRate: reading.referenceRate,
        referenceSource: reading.source,
        target,
        sentenceIds: sentences.map((x) => x.id),
      },
      results,
    );
    log('round_end', { success_rate_pct: run.success_rate_pct, decision: run.decision });
    onRoundEnd(run);
  }

  async function exit() {
    if (guideTimer.current) clearTimeout(guideTimer.current);
    const left = await recorder.current?.release();
    if (left) await discardRecordingFile(left.fileUri);
    onExit();
  }

  if (phase === 'loading') {
    return (
      <Frame>
        <Text style={s.title}>노래방 따라 읽기</Text>
        <ActivityIndicator size="large" />
      </Frame>
    );
  }
  if (phase === 'denied' || phase === 'error') {
    return (
      <Frame>
        <Text style={s.title}>시작하지 못했어요</Text>
        <Text style={s.warn}>{phase === 'denied' ? '마이크 권한이 필요해요.' : error}</Text>
        <Button label="나가기" onPress={() => void exit()} />
      </Frame>
    );
  }

  const last = results[results.length - 1];
  return (
    <Frame>
      <Text style={s.progressLabel}>
        문장 {index + 1} / {sentences.length} · 단계 {level}
      </Text>
      <Text style={s.title}>안내를 따라 읽어요</Text>
      {target && (
        <Text style={s.note}>
          파란 글자가 지나가는 빠르기({target.guideRate.toFixed(1)}음절/초)에 맞춰, 같은 박자로 읽어 주세요.
        </Text>
      )}

      <GuideSentence sentence={sentence} cursor={cursor} />
      <ReadDots total={sentence.syllables} filled={phase === 'ready' ? 0 : progress} />

      {phase === 'ready' && (
        <>
          <Button label="시작" onPress={() => void start()} />
          <Button label="나가기" kind="secondary" onPress={() => void exit()} />
        </>
      )}
      {phase === 'reading' && <Text style={s.body}>{progress < 0.5 ? '준비… 파란 글자가 움직이면 읽어 주세요' : '읽는 중이에요'}</Text>}
      {phase === 'analyzing' && <ActivityIndicator size="large" />}
      {phase === 'feedback' && last && target && !last.judged && (
        <>
          <Text style={s.warn}>{REASON_TEXT.incomplete}</Text>
          <Button label="다시 읽기" onPress={retry} />
          <Button label={index + 1 < sentences.length ? '다음 문장' : '라운드 끝'} kind="secondary" onPress={next} />
        </>
      )}
      {phase === 'feedback' && last && target && last.judged && (
        <>
          <Text style={last.passed ? s.ok : s.warn}>
            {last.passed ? (last.rhythm_checked ? '잘했어요!' : '잘했어요! (박자는 확인하지 못했어요)') : REASON_TEXT[last.reason ?? 'no_speech']}
          </Text>
          <Text style={s.body}>
            빠르기 {last.articulation_rate === null ? '-' : last.articulation_rate.toFixed(1)}음절/초 (목표 {target.min.toFixed(1)}~{target.max.toFixed(1)})
          </Text>
          <Button label={index + 1 < sentences.length ? '다음 문장' : '라운드 끝'} onPress={next} />
        </>
      )}
    </Frame>
  );
}

/** 안내 커서: 음절마다 회색에서 파란색으로, 목표 속도로 지나간다. 낱말 단위로 줄을 바꾼다 */
function GuideSentence({ sentence, cursor }: { sentence: TrainingSentence; cursor: Animated.Value }) {
  let k = 0;
  return (
    <View style={s.sentenceBox}>
      {sentence.text.split(' ').map((word, w) => (
        <View key={w} style={s.word}>
          {[...word].map((ch, i) => {
            if (!isHangul(ch)) return <Text key={i} style={s.char}>{ch}</Text>;
            const idx = k++;
            const color = cursor.interpolate({ inputRange: [idx, idx + 1], outputRange: ['#9e9e9e', '#1565c0'], extrapolate: 'clamp' });
            return (
              <Animated.Text key={i} style={[s.char, { color }]}>
                {ch}
              </Animated.Text>
            );
          })}
        </View>
      ))}
    </View>
  );
}

/** 사용자 진행 표시: 읽은 음절 수 추정만큼 점을 채운다 */
function ReadDots({ total, filled }: { total: number; filled: number }) {
  return (
    <View style={s.dots}>
      {Array.from({ length: total }, (_, i) => (
        <View key={i} style={[s.dot, i < Math.round(filled) && s.dotOn]} />
      ))}
    </View>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      {children}
    </ScrollView>
  );
}

function Button({ label, onPress, kind = 'primary' }: { label: string; onPress: () => void; kind?: 'primary' | 'secondary' }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [kind === 'primary' ? s.primary : s.secondary, pressed && { opacity: 0.7 }]}>
      <Text style={kind === 'primary' ? s.primaryText : s.secondaryText}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#fff' },
  content: { padding: 24, paddingTop: 64, gap: 16 },
  progressLabel: { fontSize: 16, color: '#666' },
  title: { fontSize: 28, fontWeight: '700', color: '#111' },
  body: { fontSize: 20, lineHeight: 30, color: '#222' },
  note: { fontSize: 16, lineHeight: 24, color: '#555' },
  warn: { fontSize: 19, lineHeight: 28, color: '#b3261e' },
  ok: { fontSize: 22, color: '#1b5e20', fontWeight: '700' },
  sentenceBox: { flexDirection: 'row', flexWrap: 'wrap', padding: 20, borderRadius: 14, backgroundColor: '#f3f6fb', rowGap: 8 },
  word: { flexDirection: 'row', marginRight: 14 },
  char: { fontSize: 34, fontWeight: '700', color: '#9e9e9e' },
  dots: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  dot: { width: 16, height: 16, borderRadius: 8, backgroundColor: '#e0e0e0' },
  dotOn: { backgroundColor: '#ef6c00' },
  primary: { backgroundColor: '#1976d2', borderRadius: 14, paddingVertical: 18, alignItems: 'center' },
  secondary: { borderColor: '#1976d2', borderWidth: 2, borderRadius: 14, paddingVertical: 16, alignItems: 'center' },
  primaryText: { color: '#fff', fontSize: 20, fontWeight: '700' },
  secondaryText: { color: '#1976d2', fontSize: 18, fontWeight: '600' },
});
