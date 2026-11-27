// 화면에 보여 주는 이름과 단위(지표 정의는 docs/metrics-spec.md 3장).
// 한국어 표(*_LABELS)와 영어 표를 두고, 화면은 metricLabel() 등으로 지금 언어의 이름을 꺼낸다.

import { L } from '../i18n/lang';
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

const METRIC_LABELS_EN: Record<MetricName, string> = {
  max_phonation_time: 'Maximum phonation time',
  loudness_decay_slope: 'Loudness decay slope',
  mean_level_change: 'Mean level change',
  articulation_rate: 'Articulation rate',
  speech_rate: 'Speech rate',
  pause_ratio: 'Pause ratio',
  f0_sd_semitones: 'F0 variability (semitones)',
  pitch_range_semitones: 'Pitch range',
  sustained_level: '"Ah" loudness',
  f0_median: 'Median F0',
  noise_floor: 'Noise floor',
};

export function metricLabel(metric: MetricName): string {
  return L(METRIC_LABELS[metric], METRIC_LABELS_EN[metric]);
}

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

const UNIT_LABELS_EN: Record<Unit, string> = {
  s: 's',
  'dB/s': 'dB/s',
  dB: 'dB',
  'syllables/s': 'syll/s',
  '%': '%',
  semitones: 'st',
  dBFS: 'dBFS',
  Hz: 'Hz',
};

export function unitLabel(unit: Unit): string {
  return L(UNIT_LABELS[unit], UNIT_LABELS_EN[unit]);
}

export function formatMeasurement(m: Pick<ServerMeasurement, 'value' | 'unit' | 'low_reliability'>): string {
  const digits = m.unit === 'Hz' || m.unit === '%' ? 1 : 2;
  return `${m.value.toFixed(digits)} ${unitLabel(m.unit)}${m.low_reliability ? L(' (신뢰 낮음)', ' (low reliability)') : ''}`;
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

const STATE_LABELS_EN: Record<LocalState, string> = {
  recording: 'Recording',
  queued: 'Waiting to send',
  sending: 'Sending',
  processing: 'Server is analyzing',
  done: 'Analysis done',
  failed: 'Analysis failed',
  blocked: 'Sending error',
};

export function stateLabel(state: LocalState): string {
  return L(STATE_LABELS[state], STATE_LABELS_EN[state]);
}

/** 메인 화면 최근 회차 카드의 상태 */
export type CardStatus = 'sending' | 'processing' | 'result' | 'unsent';

export const CARD_LABELS: Record<CardStatus, string> = {
  sending: '보내는 중',
  processing: '서버 계산 중',
  result: '결과 나옴',
  unsent: '보내지 못함',
};

const CARD_LABELS_EN: Record<CardStatus, string> = {
  sending: 'Sending',
  processing: 'Analyzing',
  result: 'Results ready',
  unsent: 'Not sent',
};

/** 회차 종류 이름 */
export function purposeLabel(purpose: SessionSummary['purpose']): string {
  if (purpose === 'retest') return L('반복 측정', 'Retest');
  if (purpose === 'agc_test') return L('폰 점검', 'Phone check');
  return L('훈련', 'Training');
}

export function cardLabel(status: CardStatus): string {
  return L(CARD_LABELS[status], CARD_LABELS_EN[status]);
}

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
