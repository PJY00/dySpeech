import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { SpaceshipBaseline } from './baseline';
import { MAX_LEVEL } from './contours';
import type { SpaceshipGameRun } from './metrics';
import ResultView from './ResultView';
import SpaceshipGame from './SpaceshipGame';
import { C } from './theme';

const F0_PRESETS = [
  { label: '낮은 목소리 110Hz', hz: 110 },
  { label: '중간 150Hz', hz: 150 },
  { label: '높은 목소리 210Hz', hz: 210 },
];

type View_ = { name: 'setup' } | { name: 'game'; level: number; round: number; key: number } | { name: 'result'; run: SpaceshipGameRun };

export default function SpaceshipDemo() {
  const [f0, setF0] = useState(150);
  const [noise, setNoise] = useState(-60);
  const [level, setLevel] = useState(1);
  const [view, setView] = useState<View_>({ name: 'setup' });
  const [round, setRound] = useState(1);
  const baseline: SpaceshipBaseline = { f0MedianHz: f0, noiseFloorDbfs: noise };

  if (view.name === 'game') {
    return (
      <SpaceshipGame
        key={view.key}
        baseline={baseline}
        level={view.level}
        roundIndex={view.round}
        onFinish={(run) => {
          console.log('[spaceship game_run]', JSON.stringify(run));
          setRound((n) => n + 1);
          setView({ name: 'result', run });
        }}
        onExit={() => setView({ name: 'setup' })}
      />
    );
  }
  if (view.name === 'result') {
    return (
      <ResultView
        run={view.run}
        onPlayAgain={(lv) => setView({ name: 'game', level: lv, round, key: Date.now() })}
        onExit={() => setView({ name: 'setup' })}
      />
    );
  }
  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Text style={s.title}>우주선 게임 시험</Text>
      <Text style={s.note}> 평소 말하는 높이에 가까운 값을 고르세요.</Text>

      <Text style={s.label}>기본주파수 중앙값 (f0_median)</Text>
      <View style={s.row}>
        {F0_PRESETS.map((p) => (
          <Chip key={p.hz} on={f0 === p.hz} text={p.label} onPress={() => setF0(p.hz)} />
        ))}
      </View>
      <View style={s.row}>
        <Chip text="−10Hz" onPress={() => setF0((v) => Math.max(75, v - 10))} />
        <Text style={s.value}>{f0} Hz</Text>
        <Chip text="+10Hz" onPress={() => setF0((v) => Math.min(500, v + 10))} />
      </View>

      <Text style={s.label}>잡음 바닥 (noise_floor)</Text>
      <View style={s.row}>
        <Chip text="−5dB" onPress={() => setNoise((v) => v - 5)} />
        <Text style={s.value}>{noise} dBFS</Text>
        <Chip text="+5dB" onPress={() => setNoise((v) => v + 5)} />
      </View>
      <Text style={s.note}>게임 중 쉬는 구간에서 자동으로 다시 맞춰져요.</Text>

      <Text style={s.label}>레벨</Text>
      <View style={s.row}>
        {Array.from({ length: MAX_LEVEL }, (_, i) => i + 1).map((lv) => (
          <Chip key={lv} on={level === lv} text={String(lv)} onPress={() => setLevel(lv)} />
        ))}
      </View>

      <Pressable style={s.primary} onPress={() => setView({ name: 'game', level, round, key: Date.now() })}>
        <Text style={s.primaryText}>시작</Text>
      </Pressable>
    </ScrollView>
  );
}

function Chip({ text, on, onPress }: { text: string; on?: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={[s.chip, on && s.chipOn]}>
      <Text style={[s.chipText, on && s.chipTextOn]}>{text}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.space },
  content: { paddingTop: 64, paddingBottom: 40, paddingHorizontal: 20, gap: 12 },
  title: { color: C.text, fontSize: 26, fontWeight: '800' },
  label: { color: C.text, fontSize: 16, fontWeight: '700', marginTop: 12 },
  note: { color: C.dim, fontSize: 14, lineHeight: 20 },
  row: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  value: { color: C.text, fontSize: 18, fontWeight: '700', minWidth: 96, textAlign: 'center', fontVariant: ['tabular-nums'] },
  chip: { paddingVertical: 10, paddingHorizontal: 14, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)' },
  chipOn: { backgroundColor: C.laneEdge, borderColor: C.laneEdge },
  chipText: { color: C.text, fontSize: 15 },
  chipTextOn: { color: '#fff', fontWeight: '700' },
  primary: { marginTop: 20, backgroundColor: C.laneEdge, borderRadius: 12, paddingVertical: 16, alignItems: 'center' },
  primaryText: { color: '#fff', fontSize: 17, fontWeight: '700' },
});
