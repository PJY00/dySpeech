// 화면에 보여 주는 이름과 단위(지표 정의는 docs/metrics-spec.md 3장).

import type { LocalState, MetricName, ServerMeasurement, SessionSummary, Unit } from '../session/types';

export const METRIC_LABELS: Record<MetricName, string> = {
  max_phonation_time: '최대발성지속시간',
  loudness_decay_slope: '음량 감쇠 기울기',
  mean_level_change: '평균 음량 변화',
  articulation_rate: '조음속도',
  speech_rate: '전체 말속도',
  pause_ratio: '무음 비율',
  f0_sd_semitones: '기본주파수 반음 표준편차',
  pitch_range_semitones: '음도 범위',
  sustained_level: '"아" 음량',
  f0_median: '기본주파수 중앙값',
  noise_floor: '잡음 바닥',
};

const UNIT_LABELS: Record<Unit, string> = {
  s: '초',
  'dB/s': 'dB/초',
  dB: 'dB',
  'syllables/s': '음절/초',
  '%': '%',
  semitones: '반음',
  dBFS: 'dBFS',
  Hz: 'Hz',
};

export function formatMeasurement(m: Pick<ServerMeasurement, 'value' | 'unit' | 'low_reliability'>): string {
  const digits = m.unit === 'Hz' || m.unit === '%' ? 1 : 2;
  return `${m.value.toFixed(digits)} ${UNIT_LABELS[m.unit]}${m.low_reliability ? ' (신뢰 낮음)' : ''}`;
}

export const STATE_LABELS: Record<LocalState, string> = {
  recording: '기록 중',
  queued: '보내기 기다리는 중',
  sending: '보내는 중',
  processing: '서버 계산 중',
  done: '서버 계산 끝',
  failed: '서버 계산 실패',
  blocked: '보내기 오류',
};

/** 메인 화면 최근 회차 카드의 상태 */
export type CardStatus = 'sending' | 'processing' | 'result' | 'unsent';

export const CARD_LABELS: Record<CardStatus, string> = {
  sending: '보내는 중',
  processing: '서버 계산 중',
  result: '결과 나옴',
  unsent: '보내지 못함',
};

/**
 * 카드 상태. complete 전(recording)은 카드로 보이지 않는다(앱 시작 때 따로 묻는다)
 * - 보내는 중: sending, 오류 없이 기다리는 queued
 * - 보내지 못함: 오류가 남은 queued(다음에 앱이 앞으로 오거나 다시 열 때 이어 보냄), blocked
 * - 결과 나옴: done, failed(결과 화면에 오류 코드)
 */
export function cardStatus(s: Pick<SessionSummary, 'state' | 'lastErrorCode'>): CardStatus | null {
  switch (s.state) {
    case 'recording':
      return null;
    case 'sending':
      return 'sending';
    case 'queued':
      return s.lastErrorCode ? 'unsent' : 'sending';
    case 'blocked':
      return 'unsent';
    case 'processing':
      return 'processing';
    case 'done':
    case 'failed':
      return 'result';
  }
}

export interface SessionCard {
  summary: SessionSummary;
  status: CardStatus;
}

/** 최근 회차 카드(새 회차부터 n개) */
export function recentCards(list: SessionSummary[], n = 3): SessionCard[] {
  const cards: SessionCard[] = [];
  for (const summary of [...list].sort((a, b) => b.startedAt.localeCompare(a.startedAt))) {
    const status = cardStatus(summary);
    if (status) cards.push({ summary, status });
  }
  return cards.slice(0, n);
}

/** 메인에 있는 동안 4초 간격으로 갱신할지: 보내는 중이거나 계산 중인 카드가 있으면 */
export function needsPolling(cards: SessionCard[]): boolean {
  return cards.some((c) => c.status === 'sending' || c.status === 'processing');
}
