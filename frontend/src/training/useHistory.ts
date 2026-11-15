// 진전도(GET /v1/users/{id}/history)를 화면에 들어올 때마다 읽는다.
// 서버 주소·앱 키가 없거나 아직 서버에 사용자가 없으면 history는 null이다.

import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';

import { apiConfig, getHistory } from '../session';
import type { History, MetricName } from '../session';

export type HistoryMetric = History['metrics'][number];

export function useHistory() {
  const [history, setHistory] = useState<History | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      if (!apiConfig.url || !apiConfig.hasKey) {
        setError('서버 주소가 설정되지 않았어요');
        return;
      }
      let alive = true;
      setLoading(true);
      getHistory()
        .then((h) => {
          if (!alive) return;
          setHistory(h);
          setError(null);
        })
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => alive && setLoading(false));
      return () => {
        alive = false;
      };
    }, []),
  );

  return { history, loading, error };
}

export function findMetric(history: History | null, metric: MetricName): HistoryMetric | undefined {
  return history?.metrics.find((m) => m.metric === metric);
}

/** 기준선 대비 변화율(%) 계열. 기준선이 0이거나 없으면 빈 목록 */
export function percentSeries(m: HistoryMetric | undefined): number[] {
  const base = m?.baseline?.value;
  if (!m || base === undefined || base === 0) return [];
  return m.series.map((p) => ((p.value - base) / Math.abs(base)) * 100);
}

export interface SessionRow {
  sessionId: string;
  startedAt: string;
  values: Partial<Record<MetricName, number>>;
}

/** 회차별 줄(오래된 회차부터). 진전도 지표의 series를 회차 ID로 묶는다 */
export function sessionRows(history: History | null): SessionRow[] {
  const rows = new Map<string, SessionRow>();
  for (const m of history?.metrics ?? []) {
    for (const p of m.series) {
      const row = rows.get(p.session_id) ?? { sessionId: p.session_id, startedAt: p.started_at, values: {} };
      row.values[m.metric] = p.value;
      rows.set(p.session_id, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.startedAt.localeCompare(b.startedAt));
}
