// 라운드 끝 화면에 보여 줄 판정 한 줄(풍선·촛불). 서버로 가는 decision_reason과 따로, 지금 화면 언어로 만든다.
import { L } from '../../i18n/lang';
import type { GameRun } from '../../session/types';

interface BalloonRunView {
  params?: { target_hold_s?: number };
  result?: { best_hold_s?: number; voiced_coverage_pct?: number };
}

export function describeBalloonRun(run: GameRun): string | null {
  const { params, result } = run as GameRun & BalloonRunView;
  if (!result) return null;
  if (run.decision === 'deferred') {
    const cov = Math.round(result.voiced_coverage_pct ?? 0);
    return L(`소리가 들린 시간이 ${cov}%라 이번 라운드는 세지 않았어요`, `Voice heard ${cov}% of the time, so this round was not counted`);
  }
  if (result.best_hold_s === undefined || params?.target_hold_s === undefined) return null;
  return L(
    `가장 오래 유지 ${result.best_hold_s}초 / 목표 ${params.target_hold_s}초`,
    `Longest hold ${result.best_hold_s} s / goal ${params.target_hold_s} s`,
  );
}
