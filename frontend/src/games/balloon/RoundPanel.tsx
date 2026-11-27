// 풍선·촛불이 같이 쓰는 화면 조각: 단추, 상태 안내, 단계 라벨, 라운드 결과 요약
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { L } from '../../i18n';
import { getLang } from '../../i18n/lang';
import { BalloonTarget, getLevel, levelLabel } from './balloonLogic';
import type { LevelLabel } from './balloonLogic';
import type { BalloonGameRun } from './balloonRun';
import type { RoundPhase } from './useBalloonRound';

export function Button({ label, onPress, light }: { label: string; onPress: () => void; light?: boolean }) {
  return (
    <Pressable style={[s.button, light && s.buttonLight]} onPress={onPress}>
      <Text style={[s.buttonText, light && s.buttonTextLight]}>{label}</Text>
    </Pressable>
  );
}

/** 단계 라벨. 영어는 balloonLogic의 문구, 한국어는 같은 값으로 여기서 만든다 */
function levelText(level: number, maxPhonationTimeS?: number | null): LevelLabel {
  if (getLang() === 'en') return levelLabel(level, maxPhonationTimeS);
  const cur = getLevel(level, maxPhonationTimeS);
  const goal = `기준 +${cur.deltaDb}dB · ${cur.holdSeconds}초 유지`;
  if (cur.level <= 1) return { goal, change: null };
  const prev = getLevel(cur.level - 1, maxPhonationTimeS);
  const parts: string[] = [];
  const dHold = Math.round((cur.holdSeconds - prev.holdSeconds) * 10) / 10;
  if (dHold > 0) parts.push(`유지 시간 +${dHold}초`);
  const dDb = cur.deltaDb - prev.deltaDb;
  if (dDb > 0) parts.push(`음량 +${dDb}dB`);
  return { goal, change: parts.length ? `지난 단계보다 ${parts.join(', ')}` : null };
}

/** 판정 이유(서버로 가는 값은 영어 그대로). 한국어 화면에서만 바꿔 보여 준다 */
const REASON_KO: Record<string, string> = {
  "We couldn't hear you well, so this one wasn't counted": '소리가 잘 들리지 않아 이번 라운드는 세지 않았어요',
  'Try this level once more': '이 단계를 한 번 더 해 봐요',
  'Goal reached - moving up a level': '목표 달성! 한 단계 올라가요',
  'Goal reached. This is the highest level': '목표 달성! 가장 높은 단계예요',
};

export function reasonLabel(reason: string): string {
  return getLang() === 'en' ? reason : (REASON_KO[reason] ?? reason);
}

/** 이 단계의 목표와, 이전 단계와 달라진 점. 단계마다 무엇이 달라지는지 보이게 한다 */
export function LevelBadge({ level, maxPhonationTimeS }: { level: number; maxPhonationTimeS?: number | null }) {
  const label = levelText(level, maxPhonationTimeS);
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
          {phase === 'denied'
            ? L('마이크 권한이 필요해요. 설정에서 마이크를 허용해 주세요.', 'Microphone permission is needed. Please allow the microphone in Settings.')
            : `${L('마이크를 쓸 수 없어요.', 'Could not use the microphone.')} ${error ?? ''}`}
        </Text>
        <Button label={L('나가기', 'Exit')} onPress={onExit} />
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
    run.decision === 'deferred'
      ? L('소리가 잘 들리지 않았어요. 한 번 더 해 볼까요?', "We couldn't hear you well. Want to try once more?")
      : r.passed
        ? L('목표 달성! 🎉', 'You reached the goal! 🎉')
        : L('잘했어요!', 'Great effort!');
  return (
    <View style={s.panel}>
      <Text style={s.panelTitle}>{title}</Text>
      <Row
        label={L('목표', 'Goal')}
        value={L(
          `기준보다 +${target.deltaDb}dB 이상으로 ${target.holdSeconds}초`,
          `At least +${target.deltaDb} dB above your baseline for ${target.holdSeconds} s`,
        )}
      />
      <Row label={L('가장 오래 유지', 'Longest hold')} value={`${r.best_hold_s}${L('초', ' s')}`} />
      <Row
        label={L('소리 내는 동안 (기준 대비)', 'While you were making sound (vs. baseline)')}
        value={
          r.mean_diff_db === null || r.median_diff_db === null
            ? L('소리를 찾지 못했어요', 'No sound detected')
            : L(
                `평균 ${sign(r.mean_diff_db)}dB · 중앙값 ${sign(r.median_diff_db)}dB`,
                `Average ${sign(r.mean_diff_db)} dB · Median ${sign(r.median_diff_db)} dB`,
              )
        }
      />
      <Row label={L('목표를 넘은 시간', 'Time above the goal')} value={`${Math.round(r.above_target_pct)}%`} />
      {run.decision_reason ? <Text style={s.note}>{reasonLabel(run.decision_reason)}</Text> : null}
      <Button label={L('계속', 'Continue')} onPress={onContinue} />
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
