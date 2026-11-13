import { memo, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { Animated, AppState, Easing, Platform, Pressable, StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import {
  CLARITY_THRESHOLD, DEFAULT_MAX_HZ, DEFAULT_MIN_HZ, REALTIME_HOP_SEC, WINDOW_SEC,
} from './audio/PitchTracker';
import { useSpaceshipMic } from './audio/useSpaceshipMic';
import { VOICING_MARGIN_DB, type SpaceshipBaseline } from './baseline';
import { buildRound, nextPhrase, phraseAt, PATTERNS, REACTION_ALLOWANCE_SEC, shapeAt, type Round } from './contours';
import { addPitchFrame, advance, createGame, referenceSt } from './engine';
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

interface Hud {
  t: number;
  stars: number;
  combo: number;
  voiceText: string | null;
  meter: number;
  loud: boolean;
}

const PAD = 36;
const HUD_MS = 200;
const METER_RANGE_DB = 40;
const SHIP_W = 40;

function relativeTarget(round: Round, t: number): number | null {
  const p = phraseAt(round, t);
  if (!p) return null;
  return p.warmup ? 0 : shapeAt(round, t);
}

const Strip = memo(function Strip({ round, h }: { round: Round; h: number }) {
  const { pxPerSec, tolSt } = round.config;
  const yOf = (st: number) => h / 2 - (st / round.spanSt) * (h / 2 - PAD);
  const colW = round.columnStep * pxPerSec;
  const items: ReactElement[] = [];
  round.columns.forEach((tc, i) => {
    const c = relativeTarget(round, tc);
    if (c === null) return;
    const x = Math.round(tc * pxPerSec);
    const cw = Math.round((tc + round.columnStep) * pxPerSec) - x;
    const top = Math.round(yOf(c + tolSt));
    const bot = Math.round(yOf(c - tolSt));
    items.push(
      <View key={`l${i}`} style={[s.abs, { left: x, top, width: cw, height: bot - top, backgroundColor: C.lane }]} />,
      <View key={`a${i}`} style={[s.bullet, { left: x + colW / 2 - 6, top: top - 14 }]} />,
      <View key={`b${i}`} style={[s.bullet, { left: x + colW / 2 - 6, top: bot + 2 }]} />,
    );
  });
  return <>{items}</>;
});

const Stars = memo(function Stars({ round, h, got }: { round: Round; h: number; got: string }) {
  const { pxPerSec } = round.config;
  const yOf = (st: number) => h / 2 - (st / round.spanSt) * (h / 2 - PAD);
  return (
    <>
      {round.stars.map((ts, i) => {
        const c = relativeTarget(round, ts);
        if (c === null || got[i] === '1') return null;
        return <Text key={i} style={[s.star, { left: ts * pxPerSec - 10, top: yOf(c) - 14 }]}>★</Text>;
      })}
    </>
  );
});

export default function SpaceshipGame({ baseline, level, roundIndex = 1, onFinish, onExit, seed }: Props) {
  const round = useMemo(() => buildRound(level, undefined, seed), [level, seed]);
  const gameRef = useRef(createGame(round, baseline));
  const t0Ref = useRef<number | null>(null);
  const startedAtRef = useRef(new Date().toISOString());
  const finishedRef = useRef(false);
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;
  const [size, setSize] = useState({ w: 0, h: 0 });
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const [hud, setHud] = useState<Hud>({ t: 0, stars: 0, combo: 0, voiceText: null, meter: 0, loud: false });
  const [starsGot, setStarsGot] = useState('');

  const scrollX = useRef(new Animated.Value(0)).current;
  const shipY = useRef(new Animated.Value(0)).current;
  const shipOpacity = useRef(new Animated.Value(1)).current;
  const flameOpacity = useRef(new Animated.Value(0)).current;

  const now = () => (t0Ref.current === null ? 0 : (performance.now() - t0Ref.current) / 1000);

  const f0Range: [number, number] = [
    baseline.f0RangeHz?.min ?? DEFAULT_MIN_HZ,
    baseline.f0RangeHz?.max ?? DEFAULT_MAX_HZ,
  ];
  const mic = useSpaceshipMic(
    (f) => {
      if (t0Ref.current === null || finishedRef.current) return;
      addPitchFrame(gameRef.current, round, baseline, f, now());
    },
    { minHz: f0Range[0], maxHz: f0Range[1] },
  );
  const sampleRateRef = useRef<number | null>(null);
  sampleRateRef.current = mic.sampleRate;

  useEffect(() => {
    if (mic.status !== 'running' || t0Ref.current !== null) return;
    const anim = Animated.timing(scrollX, {
      toValue: -round.duration * round.config.pxPerSec,
      duration: round.duration * 1000,
      easing: Easing.linear,
      useNativeDriver: true,
    });
    t0Ref.current = performance.now();
    anim.start();
  }, [mic.status, round, scrollX]);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'background' || t0Ref.current === null || finishedRef.current) return;
      finishedRef.current = true;
      onExitRef.current();
    });
    return () => sub.remove();
  }, []);

  useEffect(() => {
    let id = 0;
    let lastHud = 0;
    let lastStars = -1;
    let lastY = NaN;
    let lastOpacity = -1;
    let lastFlame = -1;
    const loop = () => {
      const g = gameRef.current;
      const { h } = sizeRef.current;
      if (t0Ref.current !== null && !finishedRef.current) {
        const t = now();
        advance(g, round, t);
        const rel = g.shipSt - referenceSt(g);
        const y = h / 2 - (rel / round.spanSt) * (h / 2 - PAD);
        if (!(Math.abs(y - lastY) < 0.5)) {
          lastY = y;
          shipY.setValue(y);
        }
        const opacity = t < g.flashUntil && Math.floor(t * 8) % 2 === 0 ? 0.25 : 1;
        if (opacity !== lastOpacity) {
          lastOpacity = opacity;
          shipOpacity.setValue(opacity);
        }
        const flame = g.voiceSt !== null ? 1 : 0;
        if (flame !== lastFlame) {
          lastFlame = flame;
          flameOpacity.setValue(flame);
        }
        if (g.done) {
          finishedRef.current = true;
          onFinishRef.current(
            computeGameRun(g, round, baseline, {
              roundIndex,
              startedAt: startedAtRef.current,
              sampleRate: sampleRateRef.current,
              windowSec: WINDOW_SEC,
              hopSec: REALTIME_HOP_SEC,
              clarityThreshold: CLARITY_THRESHOLD,
              platform: `${Platform.OS} ${String(Platform.Version)}`,
              f0RangeHz: f0Range,
              reactionAllowanceSec: REACTION_ALLOWANCE_SEC,
            }),
          );
          return;
        }
        let stars = 0;
        for (const v of g.starGot) if (v) stars++;
        if (stars !== lastStars) {
          lastStars = stars;
          setStarsGot(g.starGot.map((v) => (v ? '1' : '0')).join(''));
        }
        const ms = performance.now();
        if (ms - lastHud >= HUD_MS) {
          lastHud = ms;
          setHud({
            t,
            stars,
            combo: g.combo,
            voiceText: g.voiceSt !== null ? fmtSt(g.voiceSt - referenceSt(g)) : null,
            meter: Math.min(1, Math.max(0, (g.levelDbfs - g.noise.dbfs) / METER_RANGE_DB)),
            loud: g.levelDbfs > g.noise.voicingThresholdDbfs,
          });
        }
      }
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [round, baseline, roundIndex]);

  const bgStars = useMemo(
    () => Array.from({ length: 20 }, (_, i) => ({
      x: ((i * 7919) % 997) / 997,
      y: ((i * 104729) % 991) / 991,
      r: i % 5 === 0 ? 2 : 1,
    })),
    [],
  );

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize({ w: width, h: height });
  };

  const { w, h } = size;
  const t = hud.t;
  const shipX = Math.min(84, w * 0.2);
  const cur = phraseAt(round, t);
  const next = nextPhrase(round, t);
  const countdown = mic.status === 'running' && t < round.leadIn ? Math.ceil(round.leadIn - t) : null;
  const progress = Math.min(1, t / round.duration);
  const thresholdMark = VOICING_MARGIN_DB / METER_RANGE_DB;

  return (
    <View style={s.root}>
      <View style={s.play} onLayout={onLayout}>
        {bgStars.map((b, i) => (
          <View key={`bg${i}`} style={[s.bgStar, { left: b.x * w, top: b.y * h, width: b.r, height: b.r }]} />
        ))}
        {h > 0 && (
          <>
            <View style={[s.baseline, { top: h / 2 }]} />
            <Text style={[s.baselineLabel, { top: h / 2 + 4 }]}>편한 높이</Text>
            <Animated.View style={[s.abs, { left: shipX, top: 0, height: h, width: (round.duration + round.config.phraseSec) * round.config.pxPerSec, transform: [{ translateX: scrollX }] }]}>
              <Strip round={round} h={h} />
              <Stars round={round} h={h} got={starsGot} />
            </Animated.View>
            <Animated.View
              style={[s.abs, { left: shipX - SHIP_W / 2 - 4, top: -14, opacity: shipOpacity, transform: [{ translateY: shipY }] }]}
            >
              <Animated.View style={[s.flame, { opacity: flameOpacity }]} />
              <View style={s.shipBody} />
              <View style={s.cockpit} />
            </Animated.View>
          </>
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
          <Text style={s.starCount}>★ {hud.stars}</Text>
        </View>
        <View style={s.phraseBox} pointerEvents="none">
          {cur ? (
            <>
              <Text style={s.phraseLabel}>{PATTERNS[cur.pattern].label}</Text>
              <Text style={s.phraseHint}>{hud.combo >= 5 ? `연속 ${hud.combo}` : PATTERNS[cur.pattern].hint}</Text>
            </>
          ) : next && countdown === null && mic.status === 'running' ? (
            <>
              <Text style={s.phraseNext}>숨 고르기 · 다음은 {PATTERNS[next.pattern].label}</Text>
              <Text style={s.phraseHint}>{PATTERNS[next.pattern].hint}</Text>
            </>
          ) : null}
        </View>

        {countdown !== null && (
          <View style={s.overlay} pointerEvents="none">
            <Text style={s.countdown}>{countdown}</Text>
            <Text style={s.overlayText}>
              목소리 높이로 우주선을 움직여요.{'\n'}높은 소리는 위로, 낮은 소리는 아래로.{'\n'}처음엔 편한 높이로 길게 "아—" 해 주세요.
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
          <View style={[s.meterFill, { width: `${hud.meter * 100}%`, backgroundColor: hud.loud ? C.ship : C.dim }]} />
          <View style={[s.meterMark, { left: `${thresholdMark * 100}%` }]} />
        </View>
        <Text style={s.footerText}>{hud.voiceText ? `목소리 ${hud.voiceText}` : '소리를 내면 우주선이 움직여요'}</Text>
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
  star: { position: 'absolute', fontSize: 20, color: C.star },
  shipBody: {
    width: 0, height: 0,
    borderTopWidth: 14, borderBottomWidth: 14, borderLeftWidth: SHIP_W,
    borderTopColor: 'transparent', borderBottomColor: 'transparent', borderLeftColor: C.ship,
  },
  cockpit: { position: 'absolute', left: 10, top: 11, width: 7, height: 6, borderRadius: 3, backgroundColor: C.space },
  flame: { position: 'absolute', top: 10, left: -18, width: 18, height: 8, borderRadius: 4, backgroundColor: C.flame },
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
    ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center',
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
