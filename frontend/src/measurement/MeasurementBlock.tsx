// 측정 블록 화면: (첫 회차) 캘리브레이션 → 조용히 2초 → "아" 2~3회 → 평가 문장 s1.
// 녹음마다 검사하고, 끝나면 기기 기준값과 device_measurements를 세션에 저장한 뒤 onDone(기준값)을 부른다.
// 화면 모양은 앱 시안(홈·기록·설정 탭)의 카드·버튼·목록 줄과 맞춘다.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentProps, ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';

import { Card } from '@/components/Card';
import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { colors, radius, spacing } from '@/theme';

import { discardRecordingFile, readRecordingFile, RecordingRejected, uuidv4 } from '../session';
import type { Baselines, HzRange, RecordingInput, Session } from '../session';
import { computeBlock, median } from './analysis';
import { AutoStop, checkRecording } from './checks';
import type { StepAnalysis, StepCheck } from './checks';
import { checkInputDevice, MeasurementRecorder, prepareMicrophone } from './recorder';
import { blockSteps, EVAL_SENTENCE, STEP_SPECS, trialNo } from './steps';
import type { StepId } from './steps';

export interface MeasurementBlockProps {
  session: Session;
  /** 기준값 계산까지 끝나면 부른다. 세션 complete는 부른 쪽이 한다 */
  onDone: (baselines: Baselines) => void;
  /** 사용자가 그만두면 부른다. 세션을 버릴지는 부른 쪽이 정한다 */
  onCancel?: () => void;
  /** "아" 횟수(2~3, 기본 3) */
  trials?: number;
  /** 측정이 끝난 화면의 버튼 이름(기본 '다음'). 다음에 갈 화면에 맞춰 부르는 쪽이 정한다 */
  doneLabel?: string;
}

type Phase = 'prepare' | 'steps' | 'computing' | 'summary' | 'error';
type Stage = 'intro' | 'recording' | 'checking' | 'result';

const tick = () => new Promise<void>((r) => setTimeout(r, 30));

function recordingInput(step: StepId, fileUri: string): RecordingInput {
  if (step === 'glide') return { kind: 'calibration_glide', fileUri };
  if (step === 'noise') return { kind: 'noise_floor', fileUri };
  if (step === 'sentence') return { kind: 'eval_sentence', sentenceId: EVAL_SENTENCE.id, fileUri };
  return { kind: 'mpt_trial', trialNo: trialNo(step) ?? 1, fileUri };
}

export function MeasurementBlock({ session, onDone, onCancel, trials, doneLabel = '다음' }: MeasurementBlockProps) {
  const steps = useMemo(() => blockSteps(session.needsCalibration, trials), [session, trials]);
  const [phase, setPhase] = useState<Phase>('prepare');
  const [permission, setPermission] = useState<'checking' | 'granted' | 'denied'>('checking');
  const [inputWarning, setInputWarning] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [stage, setStage] = useState<Stage>('intro');
  const [level, setLevel] = useState(-100);
  const [elapsed, setElapsed] = useState(0);
  const [check, setCheck] = useState<StepCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ baselines: Baselines; notes: string[] } | null>(null);

  const recorder = useRef<MeasurementRecorder | null>(null);
  const pendingFile = useRef<string | null>(null);
  const analyses = useRef(new Map<StepId, StepAnalysis>());
  const noiseFloor = useRef<number | null>(null);
  const glideRange = useRef<HzRange | null>(null);
  const autoStop = useRef<AutoStop | null>(null);
  const stopping = useRef(false);
  const uiCounter = useRef(0);

  const step = steps[index];
  const spec = STEP_SPECS[step];

  useEffect(() => {
    void (async () => {
      try {
        const p = await prepareMicrophone();
        setPermission(p);
        if (p !== 'granted') return;
        const input = await checkInputDevice();
        await session.setInputDevice(input.inputDevice);
        console.log('[measurement] input', JSON.stringify(input));
        if (!input.wiredHeadset) setInputWarning('유선 헤드셋이 연결되지 않은 것 같아요. 유선 헤드셋을 쓰면 더 정확하게 잴 수 있어요.');
        recorder.current = new MeasurementRecorder();
      } catch (e) {
        fail(e);
      }
    })();
    return () => {
      void (async () => {
        const left = await recorder.current?.release();
        if (left) await discardRecordingFile(left.fileUri);
        if (pendingFile.current) await discardRecordingFile(pendingFile.current);
      })();
    };
  }, []);

  function fail(e: unknown) {
    const message = e instanceof Error ? e.message : String(e);
    console.log('[measurement] error', message);
    setError(message);
    setPhase('error');
  }

  async function startRecording() {
    if (!recorder.current) return;
    try {
      autoStop.current = new AutoStop({
        floorDbfs: noiseFloor.current,
        silenceStopS: spec.silenceStopS,
        minVoiceS: spec.minVoiceS,
        maxS: spec.fixedS ?? spec.maxS,
      });
      stopping.current = false;
      setCheck(null);
      setElapsed(0);
      setStage('recording');
      await recorder.current.start(uuidv4(), (lvl, dt) => {
        const stop = autoStop.current?.push(lvl, dt) ?? false;
        uiCounter.current += 1;
        if (uiCounter.current % 5 === 0) {
          setLevel(lvl);
          setElapsed(autoStop.current?.elapsed ?? 0);
        }
        if (stop) void stopRecording();
      });
      console.log('[measurement] record_start', step);
    } catch (e) {
      fail(e);
    }
  }

  async function stopRecording() {
    if (stopping.current || !recorder.current) return;
    stopping.current = true;
    try {
      setStage('checking');
      const file = await recorder.current.stop();
      pendingFile.current = file.fileUri;
      await tick();
      const bytes = await readRecordingFile(file.fileUri);
      const c = checkRecording(step, bytes, {
        noiseFloorDbfs: noiseFloor.current,
        calibration: glideRange.current ?? session.calibration(),
      });
      console.log('[measurement] checked', step, JSON.stringify({ ok: c.ok, message: c.message, duration_s: c.analysis?.durationS }));
      setCheck(c);
      setStage('result');
    } catch (e) {
      fail(e);
    }
  }

  async function accept() {
    const uri = pendingFile.current;
    if (!uri || !check?.analysis) return;
    try {
      await session.addRecording(recordingInput(step, uri));
    } catch (e) {
      if (e instanceof RecordingRejected) {
        setCheck({ ok: false, fatal: true, message: `녹음 파일 형식이 맞지 않아요: ${e.message}`, analysis: null });
        return;
      }
      fail(e);
      return;
    }
    pendingFile.current = null;
    analyses.current.set(step, check.analysis);
    if (step === 'noise') noiseFloor.current = median(check.analysis.levels);
    if (step === 'glide') glideRange.current = check.analysis.calibration;
    await next();
  }

  async function retry() {
    if (pendingFile.current) await discardRecordingFile(pendingFile.current);
    pendingFile.current = null;
    setCheck(null);
    setStage('intro');
  }

  async function next() {
    setCheck(null);
    if (index + 1 < steps.length) {
      setIndex(index + 1);
      setStage('intro');
      return;
    }
    await compute();
  }

  async function compute() {
    setPhase('computing');
    await tick();
    try {
      const a = analyses.current;
      const sentence = a.get('sentence');
      const trialLevels = (['mpt1', 'mpt2', 'mpt3'] as StepId[]).map((s) => a.get(s)?.levels).filter((l): l is Float64Array => !!l);
      const r = computeBlock({
        noise: a.get('noise')?.levels ?? null,
        trials: trialLevels,
        sentence: sentence ? { levels: sentence.levels, f0: sentence.f0 ?? [], syllables: EVAL_SENTENCE.syllables } : null,
        calibration: glideRange.current,
        storedCalibration: session.calibration(),
      });
      await session.setDeviceMeasurements(r.measurements);
      const baselines: Baselines = { ...r.baselines, sessionId: session.id, measuredAt: new Date().toISOString() };
      await session.setBaselines(baselines, glideRange.current ? { calibration: glideRange.current } : undefined);
      console.log('[measurement] baselines', JSON.stringify(baselines), JSON.stringify(r.notes));
      setSummary({ baselines, notes: r.notes });
      setPhase('summary');
    } catch (e) {
      fail(e);
    }
  }

  const header = (
    <ScreenHeader
      title="목소리 측정"
      back={false}
      right={
        onCancel && stage !== 'recording' && phase !== 'computing' ? (
          <Pressable onPress={onCancel} hitSlop={8}>
            <Text style={s.cancel}>그만하기</Text>
          </Pressable>
        ) : undefined
      }
    />
  );

  if (phase === 'error') {
    return (
      <Screen
        header={header}
        footer={
          <PrimaryButton
            label="이 단계 다시 하기"
            chevron={false}
            onPress={() => {
              setPhase('steps');
              void retry();
            }}
          />
        }
      >
        <Card>
          <View style={s.messageRow}>
            <Ionicons name="alert-circle" size={22} color={WARN} />
            <Text style={s.cardTitle}>문제가 생겼어요</Text>
          </View>
          <Text style={s.body}>{error}</Text>
        </Card>
      </Screen>
    );
  }

  if (phase === 'prepare') {
    return (
      <Screen
        header={header}
        footer={permission === 'granted' ? <PrimaryButton label="측정 시작" onPress={() => setPhase('steps')} /> : undefined}
      >
        <Card>
          <Text style={s.cardTitle}>오늘의 목소리를 재요</Text>
          <Text style={s.body}>
            {steps.length}가지를 차례로 녹음해요. 조용한 곳에서 유선 헤드셋을 쓰고, 마이크를 입에서 일정한 거리에 두세요.
          </Text>
        </Card>
        <Card>
          <Text style={s.sectionTitle}>측정 순서</Text>
          {steps.map((id, i) => (
            <View key={id} style={s.listRow}>
              <View style={s.listIcon}>
                <MaterialCommunityIcons name={STEP_ICONS[id]} size={18} color={colors.primary} />
              </View>
              <Text style={s.listLabel}>
                {i + 1}. {STEP_SPECS[id].title}
              </Text>
            </View>
          ))}
        </Card>
        {permission === 'checking' ? <ActivityIndicator size="large" color={colors.primary} /> : null}
        {permission === 'denied' ? <Notice text="마이크 권한이 필요해요. 설정에서 마이크를 허용해 주세요." /> : null}
        {inputWarning ? <Notice text={inputWarning} /> : null}
      </Screen>
    );
  }

  if (phase === 'computing') {
    return (
      <Screen header={header} scroll={false}>
        <View style={s.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={s.cardTitle}>결과를 계산하고 있어요</Text>
        </View>
      </Screen>
    );
  }

  if (phase === 'summary' && summary) {
    const b = summary.baselines;
    const show = (v: number | null, unit: string, digits = 1) => (v === null ? '구하지 못함' : `${v.toFixed(digits)} ${unit}`);
    return (
      <Screen header={header} footer={<PrimaryButton label={doneLabel} onPress={() => onDone(b)} />}>
        <View style={s.hero}>
          <View style={s.heroIcon}>
            <Ionicons name="checkmark" size={40} color={colors.primary} />
          </View>
          <Text style={s.heroTitle}>측정이 끝났어요</Text>
          <Text style={s.heroSub}>오늘의 기준값을 저장했어요.</Text>
        </View>
        <Card>
          <Text style={s.sectionTitle}>오늘의 기준값</Text>
          <Row label="잡음 바닥" value={show(b.noiseFloorDbfs, 'dBFS')} />
          <Row label='"아" 음량' value={show(b.sustainedLevelDbfs, 'dBFS')} />
          <Row label="최대발성지속시간" value={show(b.maxPhonationTimeS, '초')} />
          <Row label="조음속도" value={show(b.articulationRateSps, '음절/초')} />
          <Row label="기본주파수 중앙값" value={show(b.f0MedianHz, 'Hz', 0)} />
          <Row label="기본주파수 탐색 범위" value={`${b.f0SearchRangeHz.min.toFixed(0)}~${b.f0SearchRangeHz.max.toFixed(0)} Hz`} />
        </Card>
        <Text style={s.note}>기기에서 계산한 값이에요. 공식 값은 서버가 계산해요. 음량은 상대값(dBFS)이에요.</Text>
        {summary.notes.map((n) => (
          <Text key={n} style={s.note}>
            · {n}
          </Text>
        ))}
      </Screen>
    );
  }

  const floor = noiseFloor.current ?? -70;
  const meter = Math.max(0, Math.min(1, (level - floor) / (0 - floor)));
  const remaining = spec.fixedS !== undefined ? Math.max(0, spec.fixedS - elapsed) : null;

  let footer: ReactNode = null;
  if (stage === 'intro') {
    footer = (
      <View style={s.actions}>
        <PrimaryButton label={step === 'noise' ? '조용히 시작' : '녹음 시작'} chevron={false} onPress={() => void startRecording()} />
        {step === 'mpt3' ? <TextButton label="건너뛰기" onPress={() => void next()} /> : null}
      </View>
    );
  } else if (stage === 'recording' && spec.fixedS === undefined) {
    footer = <PrimaryButton label={step === 'sentence' ? '다 읽었어요' : '그만'} chevron={false} onPress={() => void stopRecording()} />;
  } else if (stage === 'result' && check) {
    footer = check.ok ? (
      <View style={s.actions}>
        <PrimaryButton label="다음" onPress={() => void accept()} />
        <TextButton label="다시 하기" onPress={() => void retry()} />
      </View>
    ) : (
      <View style={s.actions}>
        <PrimaryButton label="다시 하기" chevron={false} onPress={() => void retry()} />
        {!check.fatal && check.analysis ? <TextButton label="이대로 쓰기" onPress={() => void accept()} /> : null}
      </View>
    );
  }

  return (
    <Screen header={header} footer={footer ?? undefined}>
      <View style={s.progressRow}>
        <Text style={s.progressText}>
          {index + 1} / {steps.length} 단계
        </Text>
        <View style={s.progressTrack}>
          {steps.map((id, i) => (
            <View key={id} style={[s.progressSegment, i <= index && s.progressDone]} />
          ))}
        </View>
      </View>

      <Card>
        <View style={s.stepHead}>
          <View style={s.stepIcon}>
            <MaterialCommunityIcons name={STEP_ICONS[step]} size={26} color={colors.primary} />
          </View>
          <Text style={s.stepTitle}>{spec.title}</Text>
        </View>
        <Text style={s.body}>{spec.guide}</Text>
        {step === 'sentence' ? (
          <View style={s.sentenceBox}>
            <Text style={s.sentence}>{EVAL_SENTENCE.text}</Text>
          </View>
        ) : null}
      </Card>

      {stage === 'recording' ? (
        <Card>
          <View style={s.messageRow}>
            <View style={s.recDot} />
            <Text style={s.cardTitle}>녹음 중</Text>
          </View>
          <View style={s.meterTrack}>
            <View style={[s.meterFill, { width: `${Math.round(meter * 100)}%` }]} />
          </View>
          <Text style={s.timer}>{remaining !== null ? `${remaining.toFixed(1)}초 남음` : `${elapsed.toFixed(1)}초`}</Text>
        </Card>
      ) : null}

      {stage === 'checking' ? (
        <Card>
          <View style={s.messageRow}>
            <ActivityIndicator color={colors.primary} />
            <Text style={s.body}>녹음을 확인하고 있어요</Text>
          </View>
        </Card>
      ) : null}

      {stage === 'result' && check ? (
        <Card>
          <View style={s.messageRow}>
            <Ionicons name={check.ok ? 'checkmark-circle' : 'alert-circle'} size={22} color={check.ok ? colors.success : WARN} />
            <Text style={[s.resultText, { color: check.ok ? colors.text : WARN }]}>{check.message}</Text>
          </View>
        </Card>
      ) : null}
    </Screen>
  );
}

const WARN = '#B3261E';

type IconName = ComponentProps<typeof MaterialCommunityIcons>['name'];

const STEP_ICONS: Record<StepId, IconName> = {
  glide: 'chart-bell-curve',
  noise: 'volume-off',
  mpt1: 'microphone',
  mpt2: 'microphone',
  mpt3: 'microphone',
  sentence: 'book-open-variant',
};

function TextButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} hitSlop={8} style={({ pressed }) => pressed && s.pressed}>
      <Text style={s.textButton}>{label}</Text>
    </Pressable>
  );
}

function Notice({ text }: { text: string }) {
  return (
    <View style={s.notice}>
      <Ionicons name="alert-circle" size={20} color={WARN} />
      <Text style={s.noticeText}>{text}</Text>
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={s.rowValue}>{value}</Text>
    </View>
  );
}

// 환자가 보기 쉽게 글자는 앱의 다른 화면보다 조금 크게 둔다
const s = StyleSheet.create({
  cancel: { fontSize: 14, fontWeight: '600', color: colors.subText },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  cardTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: colors.text },
  body: { fontSize: 17, lineHeight: 26, color: colors.subText },
  note: { fontSize: 13, lineHeight: 19, color: colors.mutedText },
  messageRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  listRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  listIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  listLabel: { fontSize: 15, color: colors.text },
  hero: { alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.md },
  heroIcon: {
    width: 84,
    height: 84,
    borderRadius: 42,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroTitle: { fontSize: 24, fontWeight: '800', color: colors.text },
  heroSub: { fontSize: 14, color: colors.subText },
  progressRow: { gap: spacing.sm },
  progressText: { fontSize: 13, fontWeight: '600', color: colors.subText },
  progressTrack: { flexDirection: 'row', gap: spacing.xs },
  progressSegment: { flex: 1, height: 6, borderRadius: radius.pill, backgroundColor: colors.border },
  progressDone: { backgroundColor: colors.primary },
  stepHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  stepIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepTitle: { flex: 1, fontSize: 22, fontWeight: '800', color: colors.text },
  sentenceBox: { padding: spacing.lg, borderRadius: radius.md, backgroundColor: colors.background },
  sentence: { fontSize: 24, lineHeight: 38, fontWeight: '700', color: colors.text },
  recDot: { width: 12, height: 12, borderRadius: 6, backgroundColor: WARN },
  meterTrack: { height: 24, borderRadius: radius.pill, backgroundColor: colors.primaryLight, overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: radius.pill, backgroundColor: colors.primary },
  timer: { fontSize: 22, fontWeight: '700', fontVariant: ['tabular-nums'], color: colors.text, textAlign: 'center' },
  resultText: { flex: 1, fontSize: 17, lineHeight: 25, fontWeight: '600' },
  actions: { gap: spacing.md },
  textButton: { textAlign: 'center', fontSize: 15, fontWeight: '600', color: colors.subText },
  pressed: { opacity: 0.6 },
  notice: {
    flexDirection: 'row',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: '#FDECEA',
  },
  noticeText: { flex: 1, fontSize: 15, lineHeight: 22, color: WARN },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  rowLabel: { fontSize: 15, color: colors.subText },
  rowValue: { fontSize: 15, fontWeight: '700', color: colors.text },
});
