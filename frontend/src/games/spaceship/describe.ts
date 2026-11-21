// 라운드 끝 화면에 보여 줄 판정 한 줄(우주선, 같은 채점을 쓰는 롤러코스터). 서버로 가는 decision_reason과 따로, 지금 화면 언어로 만든다.
// success_rate_pct가 곧 길 안에 있던 비율(%)이다.
import { L } from '../../i18n/lang';
import type { GameRun } from '../../session/types';

interface LaneRunView {
  result?: { passed?: boolean; voiced_coverage_pct?: number; stars_collected?: number; stars_total?: number };
}

/** 길 따라가기 게임 공통. item은 모으는 것의 이름 [한국어, 영어] */
export function describeLaneRun(run: GameRun, item: [string, string]): string | null {
  const { result } = run as GameRun & LaneRunView;
  if (!result) return null;
  if (run.decision === 'deferred') {
    const cov = Math.round(result.voiced_coverage_pct ?? 0);
    return L(`목소리가 들린 시간이 ${cov}%라 판정하지 않았어요`, `Voice heard ${cov}% of the time, so no decision`);
  }
  const lane = Math.round(run.success_rate_pct);
  const head = result.passed ? L(`곡선 통과 · 길 안 ${lane}%`, `Curve passed · ${lane}% in the lane`) : L(`곡선 미통과 · 길 안 ${lane}%`, `Curve not passed · ${lane}% in the lane`);
  const items =
    result.stars_collected !== undefined && result.stars_total ? ` · ${L(...item)} ${result.stars_collected}/${result.stars_total}` : '';
  return head + items;
}

export function describeSpaceshipRun(run: GameRun): string | null {
  return describeLaneRun(run, ['별', 'stars']);
}
