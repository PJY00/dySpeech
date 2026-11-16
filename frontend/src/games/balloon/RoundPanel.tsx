// 풍선·촛불이 같이 쓰는 화면 조각: 단추, 상태 안내, 단계 라벨, 라운드 결과 요약
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { BalloonTarget, levelLabel } from './balloonLogic';
import type { BalloonGameRun } from './balloonRun';
import type { RoundPhase } from './useBalloonRound';

export function Button({ label, onPress, light }: { label: string; onPress: () => void; light?: boolean }) {
  return (
    <Pressable style={[s.button, light && s.buttonLight]} onPress={onPress}>
      <Text style={[s.buttonText, light && s.buttonTextLight]}>{label}</Text>
    </Pressable>
  );
}

/** 이 단계의 목표와, 이전 단계와 달라진 점. 단계마다 무엇이 달라지는지 보이게 한다 */
export function LevelBadge({ level, maxPhonationTimeS }: { level: number; maxPhonationTimeS?: number | null }) {
  const label = levelLabel(level, maxPhonationTimeS);
  return (
    <View style={s.badge}>
      <Text style={s.badgeGoal}>{label.goal}</Text>
      {label.change && <Text style={s.badgeChange}>{label.change}</Text>}
    </View>
  );
}

/** 마이크 준비 중 / 권한 거부 / 오류일 때의 안내. 해당하지 않으면 null */
export function NoticeFrame({ title, phase, error, onExit }: { title: string; phase: RoundPhase; error: string | null; onExit: () => void }) {
  if (phase === 'loading') {
    return (
      <View style={s.center}>
        <Text style={s.title}>{title}</Text>
        <ActivityIndicator />
      </View>
    );
  }
  if (phase === 'denied' || phase === 'error') {
    return (
      <View style={s.center}>
        <Text style={s.title}>{title}</Text>
        <Text style={s.note}>
          {phase === 'denied' ? 'Microphone permission is needed. Please allow the microphone in Settings.' : `Could not use the microphone. ${error ?? ''}`}
        </Text>
        <Button label="Exit" onPress={onExit} />
      </View>
    );
  }
  return null;
}

const sign = (x: number) => `${x >= 0 ? '+' : ''}${x.toFixed(1)}`;

/** 라운드 결과: 목표, 유지한 시간, 평균·중앙값, 판정 */
export function ResultPanel({ run, target, onContinue }: { run: BalloonGameRun; target: BalloonTarget; onContinue: () => void }) {
  const r = run.result;
  const title =
    run.decision === 'deferred' ? "We couldn't hear you well. Want to try once more?" : r.passed ? 'You reached the goal! 🎉' : 'Great effort!';
  return (
    <View style={s.panel}>
      <Text style={s.panelTitle}>{title}</Text>
      <Row label="Goal" value={`At least +${target.deltaDb} dB above your baseline for ${target.holdSeconds} s`} />
      <Row label="Longest hold" value={`${r.best_hold_s} s`} />
      <Row
        label="While you were making sound (vs. baseline)"
        value={r.mean_diff_db === null || r.median_diff_db === null ? 'No sound detected' : `Average ${sign(r.mean_diff_db)} dB · Median ${sign(r.median_diff_db)} dB`}
      />
      <Row label="Time above the goal" value={`${Math.round(r.above_target_pct)}%`} />
      {run.decision_reason ? <Text style={s.note}>{run.decision_reason}</Text> : null}
      <Button label="Continue" onPress={onContinue} />
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

export const common = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center', padding: 24, gap: 16 },
  title: { fontSize: 28, fontWeight: '700' },
  level: { fontSize: 14, color: '#777' },
  guide: { fontSize: 16, textAlign: 'center', color: '#333' },
  info: { fontSize: 15, textAlign: 'center', color: '#555' },
  buttons: { flexDirection: 'row', gap: 12 },
});

const s = StyleSheet.create({
  center: { flex: 1, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center', padding: 24, gap: 16 },
  title: { fontSize: 28, fontWeight: '700' },
  note: { fontSize: 15, textAlign: 'center', color: '#555' },
  badge: { alignItems: 'center', gap: 2, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10, backgroundColor: '#eef3fb' },
  badgeGoal: { fontSize: 15, fontWeight: '700', color: '#1a4a8a' },
  badgeChange: { fontSize: 13, color: '#4a6a94' },
  panel: { alignSelf: 'stretch', gap: 10, padding: 16, borderRadius: 12, backgroundColor: '#f5f5f5' },
  panelTitle: { fontSize: 20, fontWeight: '700', textAlign: 'center' },
  row: { gap: 2 },
  rowLabel: { fontSize: 13, color: '#777' },
  rowValue: { fontSize: 16, color: '#222' },
  button: { backgroundColor: '#1976d2', paddingHorizontal: 28, paddingVertical: 12, borderRadius: 8, alignItems: 'center' },
  buttonLight: { backgroundColor: '#e0e0e0' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  buttonTextLight: { color: '#333' },
});
