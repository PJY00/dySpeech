// 라운드 끝 화면에 보여 줄 판정 한 줄(속도 맞춰 읽기). 서버로 가는 decision_reason(한국어)과 따로, 지금 화면 언어로 만든다.
// 채점은 scoring.ts 그대로이고, 여기서는 문장별 결과를 세기만 한다.
import { L } from '../../i18n/lang';
import type { GameRun } from '../../session/types';

interface GuideReadingRunView {
  result?: { sentences?: { judged: boolean; passed: boolean }[] };
}

export function describeGuideReadingRun(run: GameRun): string | null {
  const sentences = (run as GameRun & GuideReadingRunView).result?.sentences;
  if (!sentences) return null;
  const judged = sentences.filter((s) => s.judged);
  const passed = judged.filter((s) => s.passed).length;
  const skipped = sentences.length - judged.length;
  const note = skipped ? L(` · 끝까지 못 읽은 ${skipped}개 제외`, ` · ${skipped} incomplete excluded`) : '';
  if (run.decision === 'deferred') {
    return L(`판정한 문장이 ${judged.length}개뿐이라 판정을 미뤘어요`, `Only ${judged.length} sentences judged, so no decision`) + note;
  }
  return L(`통과 ${passed}/${judged.length}문장`, `Passed ${passed}/${judged.length} sentences`) + note;
}
