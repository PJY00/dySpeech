// 측정 블록 화면: (첫 회차) 캘리브레이션 → 조용히 2초 → "아" 2~3회 → 평가 문장 s1.
// 녹음마다 검사하고, 끝나면 기기 기준값과 device_measurements를 세션에 저장한 뒤 onDone(기준값)을 부른다.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

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

export function MeasurementBlock({ session, onDone, onCancel, trials }: MeasurementBlockProps) {
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
        sentence: sentence ? { levels: sentence.levels, f0: sentence.f0 ?? [] } : null,
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

  if (phase === 'error') {
    return (
      <Screen>
        <Text style={s.title}>문제가 생겼어요</Text>
        <Text style={s.body}>{error}</Text>
        <Button label="이 단계 다시 하기" onPress={() => { setPhase('steps'); void retry(); }} />
        {onCancel && <Button label="그만하기" kind="secondary" onPress={onCancel} />}
      </Screen>
    );
  }

  if (phase === 'prepare') {
    return (
      <Screen>
        <Text style={s.title}>목소리 측정</Text>
        <Text style={s.body}>
          {steps.length}가지를 차례로 녹음해요. 조용한 곳에서 유선 헤드셋을 쓰고, 마이크를 입에서 일정한 거리에 두세요.
        </Text>
        {permission === 'checking' && <ActivityIndicator size="large" />}
        {permission === 'denied' && <Text style={s.warn}>마이크 권한이 필요해요. 설정에서 마이크를 허용해 주세요.</Text>}
        {inputWarning && <Text style={s.warn}>{inputWarning}</Text>}
        {permission === 'granted' && <Button label="시작" onPress={() => setPhase('steps')} />}
        {onCancel && <Button label="그만하기" kind="secondary" onPress={onCancel} />}
      </Screen>
    );
  }

  if (phase === 'computing') {
    return (
      <Screen>
        <Text style={s.title}>결과를 계산하고 있어요</Text>
        <ActivityIndicator size="large" />
      </Screen>
    );
  }

  if (phase === 'summary' && summary) {
    const b = summary.baselines;
    const show = (v: number | null, unit: string, digits = 1) => (v === null ? '구하지 못함' : `${v.toFixed(digits)} ${unit}`);
    return (
      <Screen>
        <Text style={s.title}>측정이 끝났어요</Text>
        <Row label="잡음 바닥" value={show(b.noiseFloorDbfs, 'dBFS')} />
        <Row label='"아" 음량' value={show(b.sustainedLevelDbfs, 'dBFS')} />
        <Row label="최대발성지속시간" value={show(b.maxPhonationTimeS, '초')} />
        <Row label="기본주파수 중앙값" value={show(b.f0MedianHz, 'Hz', 0)} />
        <Row label="기본주파수 탐색 범위" value={`${b.f0SearchRangeHz.min.toFixed(0)}~${b.f0SearchRangeHz.max.toFixed(0)} Hz`} />
        <Text style={s.note}>기기에서 계산한 값이에요. 공식 값은 서버가 계산해요. 음량은 상대값(dBFS)이에요.</Text>
        {summary.notes.map((n) => (
          <Text key={n} style={s.note}>
            · {n}
          </Text>
        ))}
        <Button label="다음" onPress={() => onDone(b)} />
      </Screen>
    );
  }

  const floor = noiseFloor.current ?? -70;
  const meter = Math.max(0, Math.min(1, (level - floor) / (0 - floor)));
  const remaining = spec.fixedS !== undefined ? Math.max(0, spec.fixedS - elapsed) : null;

  return (
    <Screen>
      <Text style={s.progress}>
        {index + 1} / {steps.length}
      </Text>
      <Text style={s.title}>{spec.title}</Text>
      <Text style={s.body}>{spec.guide}</Text>
      {step === 'sentence' && (
        <View style={s.sentenceBox}>
          <Text style={s.sentence}>{EVAL_SENTENCE.text}</Text>
        </View>
      )}

      {stage === 'intro' && (
        <>
          <Button label={step === 'noise' ? '조용히 시작' : '녹음 시작'} onPress={() => void startRecording()} />
          {step === 'mpt3' && <Button label="건너뛰기" kind="secondary" onPress={() => void next()} />}
        </>
      )}

      {stage === 'recording' && (
        <>
          <View style={s.meterTrack}>
            <View style={[s.meterFill, { width: `${Math.round(meter * 100)}%` }]} />
          </View>
          <Text style={s.timer}>{remaining !== null ? `${remaining.toFixed(1)}초 남음` : `${elapsed.toFixed(1)}초`}</Text>
          {spec.fixedS === undefined && <Button label={step === 'sentence' ? '다 읽었어요' : '그만'} onPress={() => void stopRecording()} />}
        </>
      )}

      {stage === 'checking' && (
        <>
          <ActivityIndicator size="large" />
          <Text style={s.body}>녹음을 확인하고 있어요</Text>
        </>
      )}

      {stage === 'result' && check && (
        <>
          <Text style={check.ok ? s.ok : s.warn}>{check.message}</Text>
          {check.ok ? (
            <>
              <Button label="다음" onPress={() => void accept()} />
              <Button label="다시 하기" kind="secondary" onPress={() => void retry()} />
            </>
          ) : (
            <>
              <Button label="다시 하기" onPress={() => void retry()} />
              {!check.fatal && check.analysis && <Button label="이대로 쓰기" kind="secondary" onPress={() => void accept()} />}
            </>
          )}
        </>
      )}

      {onCancel && stage !== 'recording' && <Button label="그만하기" kind="secondary" onPress={onCancel} />}
    </Screen>
  );
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      {children}
    </ScrollView>
  );
}

function Button({ label, onPress, kind = 'primary' }: { label: string; onPress: () => void; kind?: 'primary' | 'secondary' }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [kind === 'primary' ? s.primary : s.secondary, pressed && s.pressed]}>
      <Text style={kind === 'primary' ? s.primaryText : s.secondaryText}>{label}</Text>
    </Pressable>
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

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#fff' },
  content: { padding: 24, paddingTop: 64, gap: 16 },
  progress: { fontSize: 16, color: '#666' },
  title: { fontSize: 28, fontWeight: '700', color: '#111' },
  body: { fontSize: 20, lineHeight: 30, color: '#222' },
  note: { fontSize: 15, lineHeight: 22, color: '#555' },
  warn: { fontSize: 18, lineHeight: 26, color: '#b3261e' },
  ok: { fontSize: 20, color: '#1b5e20', fontWeight: '600' },
  sentenceBox: { padding: 20, borderRadius: 12, backgroundColor: '#f3f6fb' },
  sentence: { fontSize: 26, lineHeight: 40, fontWeight: '600', color: '#111' },
  meterTrack: { height: 28, borderRadius: 14, backgroundColor: '#e6e6e6', overflow: 'hidden' },
  meterFill: { height: '100%', backgroundColor: '#1976d2' },
  timer: { fontSize: 22, fontVariant: ['tabular-nums'], color: '#333' },
  primary: { backgroundColor: '#1976d2', borderRadius: 14, paddingVertical: 18, alignItems: 'center' },
  secondary: { borderColor: '#1976d2', borderWidth: 2, borderRadius: 14, paddingVertical: 16, alignItems: 'center' },
  pressed: { opacity: 0.7 },
  primaryText: { color: '#fff', fontSize: 20, fontWeight: '700' },
  secondaryText: { color: '#1976d2', fontSize: 18, fontWeight: '600' },
  row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#eee' },
  rowLabel: { fontSize: 18, color: '#333' },
  rowValue: { fontSize: 18, fontWeight: '700', color: '#111' },
});
