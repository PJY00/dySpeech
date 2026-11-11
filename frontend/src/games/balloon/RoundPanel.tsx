// 풍선·촛불이 같이 쓰는 화면 조각: 단추, 상태 안내, 라운드 결과 요약
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { BalloonTarget } from './balloonLogic';
import type { BalloonGameRun } from './balloonRun';
import type { RoundPhase } from './useBalloonRound';

export function Button({ label, onPress, light }: { label: string; onPress: () => void; light?: boolean }) {
  return (
    <Pressable style={[s.button, light && s.buttonLight]} onPress={onPress}>
      <Text style={[s.buttonText, light && s.buttonTextLight]}>{label}</Text>
    </Pressable>
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
          {phase === 'denied' ? '마이크 권한이 필요해요. 설정에서 허용한 뒤 다시 들어와 주세요.' : `마이크를 쓰지 못했어요. ${error ?? ''}`}
        </Text>
        <Button label="나가기" onPress={onExit} />
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
    run.decision === 'deferred' ? '소리가 잘 안 들렸어요. 한 번 더 해 볼까요?' : r.passed ? '목표를 채웠어요! 🎉' : '수고했어요!';
  return (
    <View style={s.panel}>
      <Text style={s.panelTitle}>{title}</Text>
      <Row label="목표" value={`기준보다 +${target.deltaDb}dB 이상을 ${target.holdSeconds}초 유지`} />
      <Row label="가장 오래 유지" value={`${r.best_hold_s}초`} />
      <Row
        label="소리 낸 구간(기준 대비)"
        value={r.mean_diff_db === null || r.median_diff_db === null ? '소리가 감지되지 않았어요' : `평균 ${sign(r.mean_diff_db)}dB · 중앙값 ${sign(r.median_diff_db)}dB`}
      />
      <Row label="목표 이상 유지한 비율" value={`${Math.round(r.above_target_pct)}%`} />
      {run.decision_reason ? <Text style={s.note}>{run.decision_reason}</Text> : null}
      <Button label="계속" onPress={onContinue} />
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
