// 노래방 따라 읽기(game = guide_reading, 말속도 영역). 라운드 하나 = 문장 5개.
// 안내 커서가 목표 속도로 글자 위를 지나가고, 사용자가 읽은 곳은 아래 점으로 따로 보여 준다.
// 문장을 끝내는 조건은 endRule.ts(80% 넘게 읽었으면 1초 무음, 아니면 안내가 끝난 뒤 3초 무음). 화면은 React Native 기본 애니메이션(Animated)만 쓴다.
// [Start]를 누르면 3·2·1을 보여 준 뒤 커서가 움직이고, 판정에는 커서 시작 0.5초 전부터의 소리만 쓴다(timing.ts).

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ActivityIndicator, Animated, Easing, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { L, useLang } from '../../i18n';
import type { GameProps } from '../../training/gameTypes';
import { MeasurementRecorder, prepareMicrophone } from '../../measurement/recorder';
import { discardRecordingFile, readRecordingFile, stateStore, uuidv4 } from '../../session';
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
import { COUNTDOWN_S, JudgeGate, judgedSamples, sentenceTiming } from './timing';

type Phase = 'loading' | 'denied' | 'ready' | 'reading' | 'analyzing' | 'feedback' | 'error';

const log = (event: string, data?: unknown) => console.log('[guide_reading]', event, data === undefined ? '' : JSON.stringify(data));
const isHangul = (ch: string) => {
  const c = ch.codePointAt(0) ?? 0;
  return c >= 0xac00 && c <= 0xd7a3;
};

/** 문장 피드백. 영어는 채점 모듈의 문구(REASON_TEXT)를 그대로 쓰고, 한국어는 여기 둔다 */
const REASON_TEXT_KO: Record<keyof typeof REASON_TEXT, string> = {
  fast: '조금 빨랐어요. 안내 막대에 맞춰 천천히 읽어 보세요.',
  slow: '조금 느렸어요. 안내 막대를 따라가 보세요.',
  uneven: '빠르기가 고르지 않았어요. 한 글자씩 같은 박자로 읽어 보세요.',
  no_speech: '목소리가 잘 들리지 않았어요.',
  incomplete: '끝까지 읽지 못한 것 같아요.',
};

function reasonText(reason: keyof typeof REASON_TEXT): string {
  return L(REASON_TEXT_KO[reason], REASON_TEXT[reason]);
}

export function GuideReadingGame({ baselines, level, roundIndex, userKey, onRoundEnd, onExit }: GameProps) {
  useLang();
  const sentences = useMemo(() => roundSentences(roundIndex), [roundIndex]);
  const [phase, setPhase] = useState<Phase>('loading');
  const [index, setIndex] = useState(0);
  const [reading, setReading] = useState<ReadingState | null>(null);
  const [results, setResults] = useState<SentenceResult[]>([]);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  /** 카운트다운에 보여 줄 숫자(3·2·1). 커서가 움직이면 null */
  const [countdown, setCountdown] = useState<number | null>(null);
  const cursor = useRef(new Animated.Value(0)).current;
  const recorder = useRef<MeasurementRecorder | null>(null);
  const stopping = useRef(false);
  const startedAt = useRef(new Date().toISOString());
  const gameRunId = useRef(uuidv4());
  const countdownTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const sentence = sentences[index];
  const target = reading ? targetFor(reading.referenceRate, level) : null;

  useEffect(() => {
    void (async () => {
      try {
        if ((await prepareMicrophone()) !== 'granted') {
          setPhase('denied');
          return;
        }
        const state = await loadReadingState(stateStore, userKey, baselines.articulationRateSps);
        log('reference', { user_key: userKey, ...state, level });
        setReading(state);
        recorder.current = new MeasurementRecorder();
        setPhase('ready');
      } catch (e) {
        fail(e);
      }
    })();
    return () => {
      if (countdownTimer.current) clearInterval(countdownTimer.current);
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
    const timing = sentenceTiming(s.syllables, target.guideRate);
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
      guideStartS: timing.guideStartS,
      guideS: timing.guideS,
      maxS: timing.maxS,
    });
    // 카운트다운 동안 낸 소리는 실시간 진행 표시에 넣지 않는다
    const gate = new JudgeGate(timing.judgeFromS, 48000);
    stopping.current = false;
    cursor.setValue(0);
    setProgress(0);
    setPhase('reading');
    let n = 0;
    try {
      await recorder.current.start(uuidv4(), (lvl, dt, raw) => {
        const judged = gate.take(raw);
        if (judged) live.push(judged);
        n += 1;
        if (n % 5 === 0) setProgress(live.estimate);
        if (end.push(lvl, dt, live.estimate)) {
          if (!stopping.current) log('sentence_end', { reason: end.reason, elapsed_s: end.elapsedS, estimate: live.estimate });
          void stop(live, end.reason ?? 'max', timing.judgeFromS);
        }
      });
    } catch (e) {
      fail(e);
      return;
    }
    startCountdown(s.syllables, timing.guideS);
    log('sentence_start', { sentence_id: s.id, guide_rate: target.guideRate, guide_s: timing.guideS, countdown_s: COUNTDOWN_S, judge_from_s: timing.judgeFromS });
  }

  /** 3·2·1을 1초씩 보여 준 뒤 안내 커서를 움직인다 */
  function startCountdown(syllables: number, guideS: number) {
    let left = COUNTDOWN_S;
    setCountdown(left);
    countdownTimer.current = setInterval(() => {
      left -= 1;
      if (left > 0) {
        setCountdown(left);
        return;
      }
      clearCountdown();
      Animated.timing(cursor, { toValue: syllables, duration: guideS * 1000, easing: Easing.linear, useNativeDriver: false }).start();
    }, 1000);
  }

  function clearCountdown() {
    if (countdownTimer.current) clearInterval(countdownTimer.current);
    countdownTimer.current = null;
    setCountdown(null);
  }

  async function stop(live: LiveReader, endReason: EndReason, judgeFromS: number) {
    if (stopping.current || !recorder.current || !target) return;
    stopping.current = true;
    clearCountdown();
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
      // 판정은 커서 시작 0.5초 전부터의 소리로만(카운트다운 동안 미리 읽어 본 소리는 뺌)
      const samples = judgedSamples(decodePcm16(bytes, info), info.sampleRate, judgeFromS);
      const a = analyzeSentence(samples, info.sampleRate, sentence.syllables, {
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
    clearCountdown();
    const left = await recorder.current?.release();
    if (left) await discardRecordingFile(left.fileUri);
    onExit();
  }

  if (phase === 'loading') {
    return (
      <Frame>
        <Text style={s.title}>{L('속도 맞춰 읽기', 'Pace Reading')}</Text>
        <ActivityIndicator size="large" />
      </Frame>
    );
  }
  if (phase === 'denied' || phase === 'error') {
    return (
      <Frame>
        <Text style={s.title}>{L('시작하지 못했어요', "Couldn't start")}</Text>
        <Text style={s.warn}>{phase === 'denied' ? L('마이크 권한이 필요해요.', 'Microphone access needed.') : error}</Text>
        <Button label={L('나가기', 'Exit')} onPress={() => void exit()} />
      </Frame>
    );
  }

  const last = results[results.length - 1];
  return (
    <Frame>
      <Text style={s.progressLabel}>
        {L(`문장 ${index + 1} / ${sentences.length} · 단계 ${level}`, `Sentence ${index + 1} / ${sentences.length} · Level ${level}`)}
      </Text>
      <Text style={s.title}>{L('안내를 따라 읽어요', 'Read along with the guide')}</Text>
      {target && (
        <Text style={s.note}>
          {L(
            `파란 글자가 지나가는 빠르기(${target.guideRate.toFixed(1)}음절/초)에 맞춰, 같은 박자로 읽어 주세요.`,
            `Read at the pace of the blue letters (${target.guideRate.toFixed(1)} syllables/s), keeping an even beat.`,
          )}
        </Text>
      )}

      <GuideSentence sentence={sentence} cursor={cursor} />
      <ReadDots total={sentence.syllables} filled={phase === 'ready' ? 0 : progress} />

      {phase === 'ready' && (
        <>
          <Button label={L('시작', 'Start')} onPress={() => void start()} />
          <Button label={L('나가기', 'Exit')} kind="secondary" onPress={() => void exit()} />
        </>
      )}
      {phase === 'reading' && countdown !== null && (
        <>
          <Text style={s.countdown}>{countdown}</Text>
          <Text style={s.body}>
            {L(
              '먼저 속으로 읽어 보세요. 파란 글자가 움직이면 소리 내어 읽어요.',
              'Read it silently first. Start reading aloud when the blue letters move.',
            )}
          </Text>
        </>
      )}
      {phase === 'reading' && countdown === null && (
        <Text style={s.body}>{progress < 0.5 ? L('파란 글자를 따라 읽어 주세요', 'Read along with the blue letters') : L('읽는 중이에요', 'Reading…')}</Text>
      )}
      {phase === 'analyzing' && <ActivityIndicator size="large" />}
      {phase === 'feedback' && last && target && !last.judged && (
        <>
          <Text style={s.warn}>{reasonText('incomplete')}</Text>
          <Button label={L('다시 읽기', 'Read again')} onPress={retry} />
          <Button label={index + 1 < sentences.length ? L('다음 문장', 'Next sentence') : L('라운드 끝', 'Finish round')} kind="secondary" onPress={next} />
        </>
      )}
      {phase === 'feedback' && last && target && last.judged && (
        <>
          <Text style={last.passed ? s.ok : s.warn}>
            {last.passed
              ? last.rhythm_checked
                ? L('잘했어요!', 'Well done!')
                : L('잘했어요! (박자는 확인하지 못했어요)', 'Well done! (rhythm not checked)')
              : reasonText(last.reason ?? 'no_speech')}
          </Text>
          <Text style={s.body}>
            {L(
              `빠르기 ${last.articulation_rate === null ? '-' : last.articulation_rate.toFixed(1)}음절/초 (목표 ${target.min.toFixed(1)}~${target.max.toFixed(1)})`,
              `Pace ${last.articulation_rate === null ? '-' : last.articulation_rate.toFixed(1)} syllables/s (target ${target.min.toFixed(1)}–${target.max.toFixed(1)})`,
            )}
          </Text>
          <Button label={index + 1 < sentences.length ? L('다음 문장', 'Next sentence') : L('라운드 끝', 'Finish round')} onPress={next} />
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
  countdown: { fontSize: 64, fontWeight: '800', color: '#1565c0', textAlign: 'center' },
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
