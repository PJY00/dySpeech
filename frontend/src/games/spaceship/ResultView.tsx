import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { nextLevelOf, type SpaceshipGameRun } from './metrics';
import { C } from './theme';

interface Props {
  run: SpaceshipGameRun;
  onPlayAgain: (level: number) => void;
  onExit: () => void;
}

const pct = (x: number | null) => (x === null ? '–' : `${Math.round(x)}%`);
const st = (x: number | null) => (x === null ? '–' : `${x.toFixed(2)} 반음`);

export default function ResultView({ run, onPlayAgain, onExit }: Props) {
  const r = run.result;
  const next = nextLevelOf(run);
  const rows: { label: string; value: string; note: string; warn?: boolean }[] = [
    {
      label: '곡선 편차',
      value: st(r.curve_rmse_semitones),
      note: `목표 억양과의 차이. ${r.pass_rmse_semitones.toFixed(2)} 반음 이하면 통과`,
      warn: r.curve_rmse_semitones !== null && r.curve_rmse_semitones > r.pass_rmse_semitones,
    },
    {
      label: '억양 폭',
      value: st(r.f0_sd_semitones),
      note: `내 음높이의 표준편차. 목표 곡선은 ${r.target_sd_semitones.toFixed(2)} 반음`,
    },
    {
      label: '길 안에 머문 비율',
      value: pct(run.success_rate_pct),
      note: `별 ${r.stars_collected}/${r.stars_total} · 최고 연속 ${r.best_combo}`,
    },
    {
      label: '소리 낸 구간',
      value: pct(r.voiced_coverage_pct),
      note: '목표 구간 중 음높이가 측정된 비율. 50% 이상이어야 판정해요',
      warn: r.voiced_coverage_pct < 50,
    },
    {
      label: '음높이 추정 실패율',
      value: pct(r.estimation_failure_pct),
      note: '소리는 났지만 높이를 잡지 못한 비율. 높으면 숨 섞인 목소리이거나 주변이 시끄러운 경우예요',
    },
  ];

  const title = run.decision === 'deferred' ? '소리가 잘 안 들렸어요' : r.passed ? '통과했어요' : '한 번 더 해 볼까요?';
  const sub = run.decision === 'deferred'
    ? '보라색 길이 나오는 동안 계속 "아—" 소리를 내 주세요.'
    : run.decision === 'up'
      ? `잘 따라왔어요. 다음은 레벨 ${next}에 도전해요.`
      : run.decision === 'down'
        ? `레벨 ${next}에서 다시 연습해요.`
        : r.passed ? '목표 억양을 잘 따라왔어요.' : '높낮이를 조금 더 크게 움직여 보세요.';

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Text style={s.eyebrow}>우주선 게임 · 레벨 {run.level} · {run.round_index}번째 판</Text>
      <Text style={[s.title, { color: r.passed ? C.good : C.text }]}>{title}</Text>
      <Text style={s.sub}>{sub}</Text>

      <View style={s.table}>
        {rows.map((row) => (
          <View key={row.label} style={s.row}>
            <View style={s.rowHead}>
              <Text style={s.rowLabel}>{row.label}</Text>
              <Text style={[s.rowValue, row.warn && { color: C.warn }]}>{row.value}</Text>
            </View>
            <Text style={s.rowNote}>{row.note}</Text>
          </View>
        ))}
      </View>

      <View style={s.buttons}>
        <Pressable style={s.primary} onPress={() => onPlayAgain(next)}>
          <Text style={s.primaryText}>{next === run.level ? '한 판 더' : `레벨 ${next}로 한 판 더`}</Text>
        </Pressable>
        <Pressable style={s.secondary} onPress={onExit}>
          <Text style={s.secondaryText}>끝내기</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.space },
  content: { paddingTop: 64, paddingBottom: 40, paddingHorizontal: 20, gap: 12 },
  eyebrow: { color: C.dim, fontSize: 14, letterSpacing: 0.5 },
  title: { fontSize: 30, fontWeight: '800' },
  sub: { color: C.text, fontSize: 16, lineHeight: 24 },
  table: { marginTop: 8, borderRadius: 14, backgroundColor: C.panel, paddingHorizontal: 16 },
  row: { paddingVertical: 14, gap: 4, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(255,255,255,0.12)' },
  rowHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 },
  rowLabel: { color: C.text, fontSize: 16, fontWeight: '600', flexShrink: 1 },
  rowValue: { color: C.text, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  rowNote: { color: C.dim, fontSize: 13, lineHeight: 19 },
  buttons: { marginTop: 12, gap: 10 },
  primary: { backgroundColor: C.laneEdge, borderRadius: 12, paddingVertical: 16, alignItems: 'center' },
  primaryText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  secondary: { borderRadius: 12, paddingVertical: 15, alignItems: 'center', borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)' },
  secondaryText: { color: C.text, fontSize: 16 },
});
