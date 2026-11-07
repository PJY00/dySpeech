import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { Platform, Pressable, StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import {
  CLARITY_THRESHOLD, DEFAULT_MAX_HZ, DEFAULT_MIN_HZ, HOP_SEC, WINDOW_SEC,
} from './audio/PitchTracker';
import { useMicPitch } from './audio/useMicPitch';
import { VOICING_MARGIN_DB, type SpaceshipBaseline } from './baseline';
import { buildRound, nextPhrase, phraseAt, PATTERNS, REACTION_ALLOWANCE_SEC, targetStAt } from './contours';
import { addPitchFrame, advance, createGame } from './engine';
import { computeGameRun, type SpaceshipGameRun } from './metrics';
import { C } from './theme';

interface Props {
  baseline: SpaceshipBaseline;
  level: number;
  roundIndex?: number;
  onFinish: (run: SpaceshipGameRun) => void;
  onExit: () => void;
  seed?: number;
}

const PAD = 36;
const TRAIL_SEC = 1.6;

export default function SpaceshipGame({ baseline, level, roundIndex = 1, onFinish, onExit, seed }: Props) {
  const round = useMemo(() => buildRound(level, 8, seed), [level, seed]);
  const gameRef = useRef(createGame(round, baseline));
  const t0Ref = useRef<number | null>(null);
  const startedAtRef = useRef(new Date().toISOString());
  const finishedRef = useRef(false);
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [, setFrame] = useState(0);

  const now = () => (t0Ref.current === null ? 0 : (performance.now() - t0Ref.current) / 1000);

  const f0Range: [number, number] = [
    baseline.f0RangeHz?.min ?? DEFAULT_MIN_HZ,
    baseline.f0RangeHz?.max ?? DEFAULT_MAX_HZ,
  ];
  const mic = useMicPitch(
    true,
    (f) => {
      if (t0Ref.current === null || finishedRef.current) return;
      addPitchFrame(gameRef.current, round, baseline, f, now());
    },
    { minHz: f0Range[0], maxHz: f0Range[1] },
  );
  const sampleRateRef = useRef<number | null>(null);
  sampleRateRef.current = mic.sampleRate;

  useEffect(() => {
    if (mic.status === 'running' && t0Ref.current === null) t0Ref.current = performance.now();
  }, [mic.status]);

  useEffect(() => {
    let id = 0;
    const loop = () => {
      const g = gameRef.current;
      if (t0Ref.current !== null && !finishedRef.current) {
        advance(g, round, now());
        if (g.done) {
          finishedRef.current = true;
          onFinishRef.current(
            computeGameRun(g, round, baseline, {
              roundIndex,
              startedAt: startedAtRef.current,
              sampleRate: sampleRateRef.current,
              windowSec: WINDOW_SEC,
              hopSec: HOP_SEC,
              clarityThreshold: CLARITY_THRESHOLD,
              platform: `${Platform.OS} ${String(Platform.Version)}`,
              f0RangeHz: f0Range,
              reactionAllowanceSec: REACTION_ALLOWANCE_SEC,
            }),
          );
          return;
        }
      }
      setFrame((n) => (n + 1) % 1_000_000);
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [round, baseline, roundIndex]);

  const bgStars = useMemo(
    () => Array.from({ length: 36 }, (_, i) => ({
      x: ((i * 7919) % 997) / 997,
      y: ((i * 104729) % 991) / 991,
      speed: 8 + (i % 4) * 10,
      r: i % 5 === 0 ? 2 : 1,
    })),
    [],
  );

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize({ w: width, h: height });
  };

  const g = gameRef.current;
  const { w, h } = size;
  const t = g.t;
  const { pxPerSec, tolSt } = round.config;
  const shipX = Math.min(84, w * 0.2);
  const yOf = (st: number) => h / 2 - (st / round.spanSt) * (h / 2 - PAD);
  const xOf = (time: number) => shipX + (time - t) * pxPerSec;
  const colW = round.columnStep * pxPerSec;

  const field: ReactElement[] = [];
  if (w > 0) {
    round.columns.forEach((tc, i) => {
      const x = Math.round(xOf(tc));
      const cw = Math.round(xOf(tc) + colW) - x;
      if (x < -colW || x > w + colW) return;
      const c = targetStAt(round, tc);
      if (c === null) return;
      const top = Math.round(yOf(c + tolSt));
      const bot = Math.round(yOf(c - tolSt));
      const hit = g.columnHit[i];
      field.push(
        <View key={`ht${i}`} style={[s.abs, { left: x, top: 0, width: cw, height: Math.max(0, top), backgroundColor: C.hazard }]} />,
        <View key={`hb${i}`} style={[s.abs, { left: x, top: bot, width: cw, height: Math.max(0, h - bot), backgroundColor: C.hazard }]} />,
        <View key={`ln${i}`} style={[s.abs, { left: x, top, width: cw, height: bot - top, backgroundColor: C.lane }]} />,
        <View key={`bt${i}`} style={[s.bullet, hit && s.bulletHit, { left: x + cw / 2 - 6, top: top - 14 }]} />,
        <View key={`bb${i}`} style={[s.bullet, hit && s.bulletHit, { left: x + cw / 2 - 6, top: bot + 2 }]} />,
      );
    });
    round.stars.forEach((ts, i) => {
      const x = xOf(ts);
      if (x < -20 || x > w + 20) return;
      const c = targetStAt(round, ts);
      if (c === null) return;
      if (g.starGot[i]) {
        const age = t - ts;
        if (age > 0.35) return;
        field.push(
          <Text key={`s${i}`} style={[s.star, { left: x - 12, top: yOf(c) - 16, opacity: 1 - age / 0.35, transform: [{ scale: 1 + age * 3 }] }]}>★</Text>,
        );
        return;
      }
      field.push(<Text key={`s${i}`} style={[s.star, { left: x - 10, top: yOf(c) - 14 }]}>★</Text>);
    });
    for (let k = g.frames.length - 1, n = 0; k >= 0; k--) {
      const f = g.frames[k];
      if (t - f.t > TRAIL_SEC) break;
      if (f.st === null || k % 2) continue;
      field.push(
        <View key={`v${k}`} style={[s.trail, { left: xOf(f.t) - 2, top: yOf(f.st) - 2, opacity: 1 - (t - f.t) / TRAIL_SEC }]} />,
      );
      if (++n > 40) break;
    }
  }

  const shipY = yOf(g.shipSt);
  const flashing = t < g.flashUntil && Math.floor(t * 12) % 2 === 0;
  const singing = g.voiceSt !== null;
  const cur = phraseAt(round, t);
  const next = nextPhrase(round, t);
  const countdown = t < round.leadIn ? Math.ceil(round.leadIn - t) : null;
  const progress = Math.min(1, t / round.duration);
  const starsGot = g.starGot.filter(Boolean).length;
  const METER_RANGE_DB = 40;
  const meter = Math.min(1, Math.max(0, (g.levelDbfs - g.noise.dbfs) / METER_RANGE_DB));
  const thresholdMark = VOICING_MARGIN_DB / METER_RANGE_DB;
  const loudEnough = g.levelDbfs > g.noise.voicingThresholdDbfs;

  return (
    <View style={s.root}>
      <View style={s.play} onLayout={onLayout}>
        {bgStars.map((b, i) => (
          <View
            key={`bg${i}`}
            style={[s.bgStar, {
              left: (((b.x * w - t * b.speed) % w) + w) % w || 0,
              top: b.y * h,
              width: b.r,
              height: b.r,
            }]}
          />
        ))}
        {h > 0 && (
          <>
            <View style={[s.baseline, { top: yOf(0) }]} />
            <Text style={[s.baselineLabel, { top: yOf(0) + 4 }]}>평소 높이</Text>
          </>
        )}
        {field}
        {h > 0 && (
          <View style={[s.abs, { left: shipX - 24, top: shipY - 14, opacity: flashing ? 0.25 : 1 }]}>
            {singing && <View style={[s.flame, { width: 8 + meter * 18, left: -6 - meter * 18 }]} />}
            <View style={s.shipBody} />
            <View style={s.cockpit} />
          </View>
        )}

        <View style={s.hud}>
          <Pressable onPress={onExit} style={s.exitBtn} accessibilityRole="button" accessibilityLabel="게임 나가기">
            <Text style={s.exitText}>나가기</Text>
          </Pressable>
          <View style={s.progressWrap}>
            <Text style={s.hudSmall}>레벨 {round.config.level}</Text>
            <View style={s.progressTrack}>
              <View style={[s.progressFill, { width: `${progress * 100}%` }]} />
            </View>
          </View>
          <Text style={s.starCount}>★ {starsGot}</Text>
        </View>
        <View style={s.phraseBox} pointerEvents="none">
          {cur ? (
            <>
              <Text style={s.phraseLabel}>{PATTERNS[cur.pattern].label}</Text>
              <Text style={s.phraseHint}>{g.combo >= 5 ? `연속 ${g.combo}` : PATTERNS[cur.pattern].hint}</Text>
            </>
          ) : next && countdown === null ? (
            <>
              <Text style={s.phraseNext}>숨 고르기 · 다음은 {PATTERNS[next.pattern].label}</Text>
              <Text style={s.phraseHint}>{PATTERNS[next.pattern].hint}</Text>
            </>
          ) : null}
        </View>

        {countdown !== null && mic.status === 'running' && (
          <View style={s.overlay} pointerEvents="none">
            <Text style={s.countdown}>{countdown}</Text>
            <Text style={s.overlayText}>
              목소리 높이로 우주선을 움직여요.{'\n'}높은 소리는 위로, 낮은 소리는 아래로.{'\n'}보라색 길을 따라가며 별을 모으세요.
            </Text>
          </View>
        )}
        {mic.status !== 'running' && (
          <View style={s.overlay}>
            <Text style={s.overlayTitle}>
              {mic.status === 'denied' ? '마이크 권한이 필요해요' : mic.status === 'error' ? '마이크를 켤 수 없어요' : '마이크 준비 중…'}
            </Text>
            {mic.status === 'denied' && <Text style={s.overlayText}>설정에서 dySpeech의 마이크 권한을 허용한 뒤 다시 시작해 주세요.</Text>}
            {mic.status === 'error' && <Text style={s.overlayText}>{mic.error}</Text>}
            {(mic.status === 'denied' || mic.status === 'error') && (
              <Pressable onPress={onExit} style={s.primaryBtn}><Text style={s.primaryBtnText}>돌아가기</Text></Pressable>
            )}
          </View>
        )}
      </View>

      <View style={s.footer}>
        <View style={s.meterTrack}>
          <View style={[s.meterFill, { width: `${meter * 100}%`, backgroundColor: loudEnough ? C.ship : C.dim }]} />
          <View style={[s.meterMark, { left: `${thresholdMark * 100}%` }]} />
        </View>
        <Text style={s.footerText}>
          {singing ? `목소리 ${fmtSt(g.voiceSt as number)}` : '소리를 내면 우주선이 움직여요'}
        </Text>
      </View>
    </View>
  );
}

function fmtSt(st: number): string {
  const v = Math.round(st * 10) / 10;
  return `${v > 0 ? '+' : ''}${v.toFixed(1)} 반음`;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.spaceDeep },
  play: { flex: 1, overflow: 'hidden', backgroundColor: C.space },
  abs: { position: 'absolute' },
  bgStar: { position: 'absolute', backgroundColor: '#ffffff', opacity: 0.5, borderRadius: 1 },
  baseline: { position: 'absolute', left: 0, right: 0, height: 1, backgroundColor: 'rgba(237,235,255,0.25)' },
  baselineLabel: { position: 'absolute', left: 8, fontSize: 11, color: C.dim },
  bullet: {
    position: 'absolute', width: 12, height: 12, borderRadius: 6,
    backgroundColor: C.bulletCore, borderWidth: 3, borderColor: C.bullet,
  },
  bulletHit: { backgroundColor: '#ffffff', borderColor: '#ffffff', transform: [{ scale: 1.4 }] },
  star: { position: 'absolute', fontSize: 20, color: C.star },
  trail: { position: 'absolute', width: 4, height: 4, borderRadius: 2, backgroundColor: C.voice },
  shipBody: {
    width: 0, height: 0,
    borderTopWidth: 14, borderBottomWidth: 14, borderLeftWidth: 40,
    borderTopColor: 'transparent', borderBottomColor: 'transparent', borderLeftColor: C.ship,
  },
  cockpit: { position: 'absolute', left: 10, top: 11, width: 7, height: 6, borderRadius: 3, backgroundColor: C.space },
  flame: { position: 'absolute', top: 10, height: 8, borderRadius: 4, backgroundColor: C.flame },
  hud: {
    position: 'absolute', top: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center',
    gap: 12, paddingTop: 48, paddingHorizontal: 16,
  },
  exitBtn: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8, backgroundColor: C.panel },
  exitText: { color: C.text, fontSize: 14 },
  progressWrap: { flex: 1, gap: 4 },
  hudSmall: { color: C.dim, fontSize: 12 },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: 'rgba(255,255,255,0.12)', overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: C.laneEdge },
  starCount: { color: C.star, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  phraseBox: { position: 'absolute', top: 100, left: 16, right: 16, alignItems: 'center', gap: 2 },
  phraseLabel: { color: C.text, fontSize: 26, fontWeight: '800' },
  phraseNext: { color: C.text, fontSize: 17, fontWeight: '700' },
  phraseHint: { color: C.dim, fontSize: 14 },
  overlay: {
    ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center',
    gap: 16, padding: 24, backgroundColor: 'rgba(6,5,26,0.72)',
  },
  countdown: { color: C.text, fontSize: 88, fontWeight: '800' },
  overlayTitle: { color: C.text, fontSize: 22, fontWeight: '700', textAlign: 'center' },
  overlayText: { color: C.text, fontSize: 17, lineHeight: 27, textAlign: 'center' },
  primaryBtn: { backgroundColor: C.laneEdge, paddingVertical: 14, paddingHorizontal: 28, borderRadius: 12 },
  primaryBtnText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  footer: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 32, gap: 8, backgroundColor: C.spaceDeep },
  meterTrack: { height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.1)', overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: 5 },
  meterMark: { position: 'absolute', top: 0, bottom: 0, width: 2, backgroundColor: C.warn },
  footerText: { color: C.text, fontSize: 15, fontVariant: ['tabular-nums'] },
});
