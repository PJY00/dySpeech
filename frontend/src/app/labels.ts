// 화면에 보여 주는 이름과 단위(지표 정의는 docs/metrics-spec.md 3장).

import type { LocalState, MetricName, ServerMeasurement, Unit } from '../session/types';

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
