import { useEffect, useMemo, useRef, useState } from 'react';
import { AppState, Platform, Pressable, StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import {
  CLARITY_THRESHOLD, DEFAULT_MAX_HZ, DEFAULT_MIN_HZ, REALTIME_HOP_SEC, WINDOW_SEC,
} from '../spaceship/audio/PitchTracker';
import { useSpaceshipMic } from '../spaceship/audio/useSpaceshipMic';
import { VOICING_MARGIN_DB, type SpaceshipBaseline } from '../spaceship/baseline';
import { buildRound, nextPhrase, phraseAt, PATTERNS, REACTION_ALLOWANCE_SEC } from '../spaceship/contours';
import { addPitchFrame, advance, createGame, referenceSt } from '../spaceship/engine';
import { computeGameRun } from '../spaceship/metrics';
import { toRollerCoasterRun, type RollerCoasterGameRun } from './run';
import { C } from './theme';
import { buildGauge, buildScene, isActive, speedAt, trackSt, trendAhead, type Gauge, type Scene, type Trend } from './view3d';

interface Props {
  baseline: SpaceshipBaseline;
  level: number;
  roundIndex?: number;
  onFinish: (run: RollerCoasterGameRun) => void;
  onExit: () => void;
  seed?: number;
}

interface Hud {
  t: number;
  coins: number;
  combo: number;
  voiceText: string | null;
  meter: number;
  loud: boolean;
  steer: 'up' | 'down' | 'ok' | null;
}

const HUD_MS = 200;
const VOICE_FRESH_SEC = 0.1;
const POP_SEC = 0.6;
const GAUGE_W = 92;
const GAUGE_H = 150;
const GAUGE_BACK = 0.5;
const GAUGE_AHEAD = 2.5;
const SCENE_MS = 33;
const METER_RANGE_DB = 40;

interface Frame {
  scene: Scene;
  gauge: Gauge;
  voicing: boolean;
  trend: Trend;
  popAt: number;
  t: number;
}

const EMPTY_FRAME: Frame = {
  scene: { horizonY: 0, roll: 0, speed: 1, noseY: 0, railNearY: 0, bed: '', ties: '', tiesActive: '', tiesUp: '', tiesDown: '', rails: '', pillars: '', coins: [] },
  gauge: { band: '', target: '', dotY: 0, zeroY: 0 },
  voicing: false,
  trend: 'flat',
  popAt: -Infinity,
  t: 0,
};

export default function RollerCoasterGame({ baseline, level, roundIndex = 1, onFinish, onExit, seed }: Props) {
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
  const [hud, setHud] = useState<Hud>({ t: 0, coins: 0, combo: 0, voiceText: null, meter: 0, loud: false, steer: null });
  const [frame, setFrame] = useState<Frame>(EMPTY_FRAME);
  const [shake, setShake] = useState({ dx: 0, dy: 0, hit: false });

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
    t0Ref.current = performance.now();
  }, [mic.status]);

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
    let lastScene = 0;
    let lastHit = false;
    let popAt = -Infinity;
    const known: boolean[] = [];
    const loop = () => {
      const g = gameRef.current;
      const { w, h } = sizeRef.current;
      const ms = performance.now();
      if (w > 0 && h > 0 && !finishedRef.current) {
        const t = now();
        if (t0Ref.current !== null) advance(g, round, t);
        const voicing = g.voiceSt !== null
          && t - g.lastValidT <= VOICE_FRESH_SEC
          && g.levelDbfs > g.noise.voicingThresholdDbfs;
        for (let i = 0; i < g.starGot.length; i++) {
          if (g.starGot[i] && !known[i]) {
            if (voicing) {
              known[i] = true;
              popAt = t;
            } else {
              g.starGot[i] = false;
            }
          }
        }
        const cam = g.shipSt - referenceSt(g);
        if (t0Ref.current !== null && g.done) {
          finishedRef.current = true;
          onFinishRef.current(
            toRollerCoasterRun(
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
            ),
          );
          return;
        }
        if (ms - lastScene >= SCENE_MS) {
          lastScene = ms;
          const upcoming = isActive(round, t) || isActive(round, t + 0.7);
          setFrame({
            scene: buildScene(round, t, cam, w, h, g.starGot),
            gauge: buildGauge(round, t, cam, GAUGE_W, GAUGE_H, GAUGE_AHEAD, GAUGE_BACK),
            voicing,
            trend: upcoming ? trendAhead(round, t) : 'flat',
            popAt,
            t,
          });
          const hit = t < g.flashUntil;
          const rumble = t0Ref.current === null ? 0 : Math.min(4, Math.max(0, (speedAt(round, t) - 1.2) * 8));
          const jitter = hit || rumble > 0;
          if (jitter || lastHit) {
            lastHit = jitter;
            const amp = hit ? 7 : rumble;
            setShake(jitter
              ? { dx: Math.sin(t * 57) * amp, dy: Math.cos(t * 43) * amp * 0.7, hit }
              : { dx: 0, dy: 0, hit: false });
          }
        }
        if (ms - lastHud >= HUD_MS) {
          lastHud = ms;
          let coins = 0;
          for (const v of g.starGot) if (v) coins++;
          const singing = g.voiceSt !== null;
          let steer: Hud['steer'] = null;
          if (isActive(round, t)) {
            const diff = trackSt(round, t) - cam;
            const tol = round.config.tolSt * 0.6;
            steer = diff > tol ? 'up' : diff < -tol ? 'down' : 'ok';
          }
          setHud({
            t,
            coins,
            combo: g.combo,
            voiceText: singing ? fmtSt((g.voiceSt as number) - referenceSt(g)) : null,
            meter: Math.min(1, Math.max(0, (g.levelDbfs - g.noise.dbfs) / METER_RANGE_DB)),
            loud: g.levelDbfs > g.noise.voicingThresholdDbfs,
            steer,
          });
        }
      }
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [round, baseline, roundIndex]);

  const skyDots = useMemo(
    () => Array.from({ length: 22 }, (_, i) => ({
      x: ((i * 7919) % 997) / 997,
      y: (((i * 104729) % 991) / 991) * 0.3,
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
  const horizonY = h * 0.34;
  const noseY = h * 0.82;
  const cur = phraseAt(round, t);
  const next = nextPhrase(round, t);
  const countdown = mic.status === 'running' && t < round.leadIn ? Math.ceil(round.leadIn - t) : null;
  const progress = Math.min(1, t / round.duration);
  const thresholdMark = VOICING_MARGIN_DB / METER_RANGE_DB;
  const { scene, gauge } = frame;
  const sky = scene.horizonY > 0 ? scene.horizonY : horizonY;
  const skyShift = sky - horizonY;
  const pad = w * 0.15;
  const mountains = w > 0
    ? `M${-pad} ${sky}L${-pad} ${sky - 35}L${w * 0.12} ${sky - 55}L${w * 0.25} ${sky - 25}L${w * 0.4} ${sky - 70}L${w * 0.55} ${sky - 30}L${w * 0.7} ${sky - 60}L${w * 0.85} ${sky - 20}L${w + pad} ${sky - 45}L${w + pad} ${sky}Z`
    : '';
  const fast = scene.speed > 1.25 && countdown === null;
  let streaks = '';
  if (fast && w > 0) {
    const reach = Math.hypot(w, h);
    for (let i = 0; i < 10; i++) {
      const ang = (i / 10) * Math.PI * 2 + 0.3;
      const u = (frame.t * scene.speed * 1.6 + i * 0.37) % 1;
      const r1 = reach * (0.25 + 0.5 * u);
      const r2 = r1 + reach * 0.08 * (scene.speed - 1);
      const ox = w / 2;
      const oy = sky;
      streaks += `M${(ox + Math.cos(ang) * r1).toFixed(1)} ${(oy + Math.sin(ang) * r1).toFixed(1)}L${(ox + Math.cos(ang) * r2).toFixed(1)} ${(oy + Math.sin(ang) * r2).toFixed(1)}`;
    }
  }
  const markColor = hud.steer === 'ok' ? C.good : hud.steer ? C.warn : C.dim;
  const popAge = frame.t - frame.popAt;
  const popping = popAge >= 0 && popAge < POP_SEC;
  const trendColor = frame.trend === 'up' ? C.up : C.down;
  const gaugeNowX = (GAUGE_BACK / (GAUGE_BACK + GAUGE_AHEAD)) * GAUGE_W;

  return (
    <View style={s.root}>
      <View style={s.play} onLayout={onLayout}>
        {h > 0 && (
          <>
            <View
              style={[
                StyleSheet.absoluteFill,
                {
                  transformOrigin: '50% 82%',
                  transform: [{ translateX: shake.dx }, { translateY: shake.dy }, { rotate: `${scene.roll.toFixed(2)}deg` }, { scale: 1.18 }],
                },
              ]}
            >
              {skyDots.map((d, i) => (
                <View key={`d${i}`} style={[s.skyDot, { left: d.x * w, top: d.y * h + skyShift * 0.6, width: d.r, height: d.r }]} />
              ))}
              <View style={[s.ground, { top: sky, left: -pad, right: -pad, bottom: -h }]} />
              <Svg width={w} height={h} style={StyleSheet.absoluteFill}>
                <Path d={mountains} fill={C.mountains} />
              </Svg>
              <Svg width={w} height={h} style={StyleSheet.absoluteFill}>
                <Path d={scene.pillars} fill={C.pillar} />
                <Path d={scene.bed} fill={C.bed} />
                <Path d={scene.ties} fill={C.tie} />
                <Path d={scene.tiesActive} fill={C.tieActive} />
                <Path d={scene.tiesUp} fill={C.up} />
                <Path d={scene.tiesDown} fill={C.down} />
                <Path d={scene.rails} stroke={C.rail} strokeWidth={4} strokeLinejoin="round" fill="none" />
                {scene.coins.map((c) => (
                  <Circle
                    key={c.i}
                    cx={c.x}
                    cy={c.y}
                    r={c.r}
                    fill={frame.voicing ? C.coin : C.coinOff}
                    stroke={frame.voicing ? C.coinEdge : C.coinOffEdge}
                    strokeWidth={Math.max(1, c.r * 0.25)}
                  />
                ))}
              </Svg>
            </View>
            {fast && (
              <Svg width={w} height={h} style={StyleSheet.absoluteFill}>
                <Path d={streaks} stroke="rgba(255,255,255,0.35)" strokeWidth={2} strokeLinecap="round" fill="none" />
              </Svg>
            )}
            <View style={[s.mark, { top: noseY - 2, left: 0, backgroundColor: markColor }]} />
            <View style={[s.mark, { top: noseY - 2, right: 0, backgroundColor: markColor }]} />
            <View style={[s.car, { top: noseY + 14 }]}>
              <View style={s.carBar} />
              <View style={s.carBody} />
            </View>
            {shake.hit && <View style={s.hitFrame} pointerEvents="none" />}
            {frame.trend !== 'flat' && countdown === null && (
              <View style={[s.trendBox, { top: horizonY - 64 }]} pointerEvents="none">
                <Text style={[s.trendArrow, { color: trendColor }]}>{frame.trend === 'up' ? '▲▲' : '▼▼'}</Text>
                <Text style={[s.trendText, { color: trendColor }]}>{frame.trend === 'up' ? 'CLIMB · voice up' : 'DROP · voice down'}</Text>
              </View>
            )}
            {popping && (
              <Text
                style={[s.pop, { top: noseY - 150 - popAge * 80, opacity: 1 - popAge / POP_SEC }]}
                pointerEvents="none"
              >
                +1
              </Text>
            )}
            <View style={[s.gauge, { top: horizonY + 12 }]} pointerEvents="none">
              <Svg width={GAUGE_W} height={GAUGE_H}>
                <Path d={gauge.band} fill="rgba(91,227,164,0.18)" />
                <Path d={gauge.target} stroke={C.text} strokeWidth={2} fill="none" />
                <Circle cx={gaugeNowX} cy={gauge.dotY} r={6} fill={frame.voicing ? C.accent : C.dim} stroke={C.text} strokeWidth={1.5} />
              </Svg>
              <View style={[s.gaugeNow, { left: gaugeNowX }]} />
              <Text style={s.gaugeLabel}>You · Track</Text>
            </View>
          </>
        )}

        <View style={s.hud}>
          <Pressable onPress={onExit} style={s.exitBtn} accessibilityRole="button" accessibilityLabel="Exit game">
            <Text style={s.exitText}>Exit</Text>
          </Pressable>
          <View style={s.progressWrap}>
            <Text style={s.hudSmall}>Level {round.config.level}</Text>
            <View style={s.progressTrack}>
              <View style={[s.progressFill, { width: `${progress * 100}%` }]} />
            </View>
          </View>
          <View style={s.coinCount}>
            <View style={s.coinIcon} />
            <Text style={s.coinText}>{hud.coins}</Text>
          </View>
        </View>
        <View style={s.phraseBox} pointerEvents="none">
          {cur ? (
            <>
              <Text style={s.phraseLabel}>{PATTERNS[cur.pattern].label}</Text>
              <Text style={s.phraseHint}>{hud.combo >= 5 ? `Smooth ride ${hud.combo}` : PATTERNS[cur.pattern].hint}</Text>
            </>
          ) : next && countdown === null && mic.status === 'running' ? (
            <>
              <Text style={s.phraseNext}>Take a breath · Next: {PATTERNS[next.pattern].label}</Text>
              <Text style={s.phraseHint}>{PATTERNS[next.pattern].hint}</Text>
            </>
          ) : null}
        </View>
        {h > 0 && hud.steer && hud.steer !== 'ok' && countdown === null && (
          <Text style={[s.steer, { top: noseY - 64 }]} pointerEvents="none">
            {hud.steer === 'up' ? '▲ Higher' : '▼ Lower'}
          </Text>
        )}

        {countdown !== null && (
          <View style={s.overlay} pointerEvents="none">
            <Text style={s.countdown}>{countdown}</Text>
            <Text style={s.overlayText}>
              You're in the front seat.{'\n'}Your voice keeps the car on the track.{'\n'}Green track climbs: go higher. Blue track drops: go lower.{'\n'}Coins only count while you make sound.{'\n'}Start with a long, comfortable "ahh".
            </Text>
          </View>
        )}
        {mic.status !== 'running' && (
          <View style={s.overlay}>
            <Text style={s.overlayTitle}>
              {mic.status === 'denied' ? 'Microphone access needed' : mic.status === 'error' ? 'Can\'t turn on the microphone' : 'Getting the microphone ready…'}
            </Text>
            {mic.status === 'denied' && <Text style={s.overlayText}>Allow microphone access for dySpeech in Settings, then start again.</Text>}
            {mic.status === 'error' && <Text style={s.overlayText}>{mic.error}</Text>}
            {(mic.status === 'denied' || mic.status === 'error') && (
              <Pressable onPress={onExit} style={s.primaryBtn}><Text style={s.primaryBtnText}>Go back</Text></Pressable>
            )}
          </View>
        )}
      </View>

      <View style={s.footer}>
        <View style={s.meterTrack}>
          <View style={[s.meterFill, { width: `${hud.meter * 100}%`, backgroundColor: hud.loud ? C.accent : C.dim }]} />
          <View style={[s.meterMark, { left: `${thresholdMark * 100}%` }]} />
        </View>
        <Text style={s.footerText}>{hud.voiceText ? `Voice ${hud.voiceText}` : 'Make a sound to steer the car'}</Text>
      </View>
    </View>
  );
}

function fmtSt(st: number): string {
  const v = Math.round(st * 10) / 10;
  return `${v > 0 ? '+' : ''}${v.toFixed(1)} semitones`;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.skyDeep },
  play: { flex: 1, overflow: 'hidden', backgroundColor: C.sky },
  skyDot: { position: 'absolute', backgroundColor: '#ffffff', opacity: 0.45, borderRadius: 1 },
  ground: { position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: C.ground },
  mark: { position: 'absolute', width: 22, height: 4, borderRadius: 2 },
  car: { position: 'absolute', left: '12%', right: '12%', bottom: -20, alignItems: 'center' },
  carBar: { width: '70%', height: 8, borderRadius: 4, backgroundColor: C.bar, marginBottom: 6 },
  carBody: {
    width: '100%', flex: 1, backgroundColor: C.car, borderTopLeftRadius: 40, borderTopRightRadius: 40,
    borderTopWidth: 6, borderColor: C.carDark,
  },
  hitFrame: { ...StyleSheet.absoluteFill, borderWidth: 6, borderColor: C.bad, opacity: 0.7 },
  trendBox: { position: 'absolute', left: 0, right: 0, alignItems: 'center' },
  trendArrow: { fontSize: 34, fontWeight: '900', letterSpacing: -4 },
  trendText: { fontSize: 15, fontWeight: '800', letterSpacing: 1 },
  pop: { position: 'absolute', left: 0, right: 0, textAlign: 'center', color: C.coin, fontSize: 34, fontWeight: '900' },
  gauge: {
    position: 'absolute', right: 10, width: GAUGE_W, height: GAUGE_H + 18,
    borderRadius: 10, backgroundColor: 'rgba(18,20,43,0.7)', overflow: 'hidden',
  },
  gaugeNow: { position: 'absolute', top: 0, height: GAUGE_H, width: 1, backgroundColor: 'rgba(255,255,255,0.35)' },
  gaugeLabel: { color: C.dim, fontSize: 10, textAlign: 'center' },
  steer: { position: 'absolute', left: 0, right: 0, textAlign: 'center', color: C.warn, fontSize: 22, fontWeight: '800' },
  hud: {
    position: 'absolute', top: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center',
    gap: 12, paddingTop: 48, paddingHorizontal: 16,
  },
  exitBtn: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8, backgroundColor: C.panel },
  exitText: { color: C.text, fontSize: 14 },
  progressWrap: { flex: 1, gap: 4 },
  hudSmall: { color: C.dim, fontSize: 12 },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: 'rgba(255,255,255,0.12)', overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: C.accent },
  coinCount: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  coinIcon: { width: 16, height: 16, borderRadius: 8, backgroundColor: C.coin, borderWidth: 3, borderColor: C.coinEdge },
  coinText: { color: C.coin, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  phraseBox: { position: 'absolute', top: 100, left: 16, right: 16, alignItems: 'center', gap: 2 },
  phraseLabel: { color: C.text, fontSize: 26, fontWeight: '800' },
  phraseNext: { color: C.text, fontSize: 17, fontWeight: '700' },
  phraseHint: { color: C.dim, fontSize: 14 },
  overlay: {
    ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center',
    gap: 16, padding: 24, backgroundColor: 'rgba(18,20,43,0.75)',
  },
  countdown: { color: C.text, fontSize: 88, fontWeight: '800' },
  overlayTitle: { color: C.text, fontSize: 22, fontWeight: '700', textAlign: 'center' },
  overlayText: { color: C.text, fontSize: 17, lineHeight: 27, textAlign: 'center' },
  primaryBtn: { backgroundColor: C.accent, paddingVertical: 14, paddingHorizontal: 28, borderRadius: 12 },
  primaryBtnText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  footer: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 32, gap: 8, backgroundColor: C.skyDeep },
  meterTrack: { height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.1)', overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: 5 },
  meterMark: { position: 'absolute', top: 0, bottom: 0, width: 2, backgroundColor: C.warn },
  footerText: { color: C.text, fontSize: 15, fontVariant: ['tabular-nums'] },
});
