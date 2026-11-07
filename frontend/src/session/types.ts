// 세션 모듈의 공개 타입. 서버 요청·응답 형식은 docs/api.md를 따른다.

export type Purpose = 'training' | 'retest' | 'agc_test';
export type RecordingKind =
  | 'calibration_glide'
  | 'noise_floor'
  | 'mpt_trial'
  | 'eval_sentence'
  | 'game'
  | 'agc_tone';
export type AgcSignal = 'agc_steps' | 'agc_hold' | 'agc_toggle';
export type SentenceId = 's1';
export type ServerStatus = 'open' | 'processing' | 'done' | 'failed';
export type Decision = 'up' | 'down' | 'hold' | 'deferred';
export type DesiredDirection = 'up' | 'down' | 'none';

export type MetricName =
  | 'max_phonation_time'
  | 'loudness_decay_slope'
  | 'mean_level_change'
  | 'articulation_rate'
  | 'speech_rate'
  | 'pause_ratio'
  | 'f0_sd_semitones'
  | 'pitch_range_semitones'
  | 'sustained_level'
  | 'f0_median'
  | 'noise_floor';
export type Unit = 's' | 'dB/s' | 'dB' | 'syllables/s' | '%' | 'semitones' | 'dBFS' | 'Hz';

/** api.md 7.1 표의 지표별 단위. 다르게 보내면 422 */
export const METRIC_UNITS: Readonly<Record<MetricName, Unit>> = {
  max_phonation_time: 's',
  loudness_decay_slope: 'dB/s',
  mean_level_change: 'dB',
  articulation_rate: 'syllables/s',
  speech_rate: 'syllables/s',
  pause_ratio: '%',
  f0_sd_semitones: 'semitones',
  pitch_range_semitones: 'semitones',
  sustained_level: 'dBFS',
  f0_median: 'Hz',
  noise_floor: 'dBFS',
};

/** 측정 블록과 게임이 세션에 넘기는 녹음. fileUri는 기기에 저장된 WAV(모노·16비트 PCM) */
export type RecordingInput =
  | { kind: 'calibration_glide' | 'noise_floor'; fileUri: string }
  | { kind: 'mpt_trial'; trialNo: number; fileUri: string }
  | { kind: 'eval_sentence'; sentenceId: SentenceId; fileUri: string }
  | { kind: 'game'; gameRunId: string; fileUri: string }
  | { kind: 'agc_tone'; agcSignal: AgcSignal; fileUri: string };

/** 게임 라운드 기록. api.md 7.2 형식 그대로(snake_case) */
export interface GameRun {
  game_run_id: string;
  game: string;
  level: number;
  round_index: number;
  started_at: string;
  params?: object;
  /** 0~100(%). 0~1 비율이 아니다 */
  success_rate_pct: number;
  decision: Decision;
  decision_reason?: string;
  result?: object;
}

/** 기기에서 계산한 지표. 서버는 저장만 한다(api.md 7.1) */
export interface DeviceMeasurement {
  metric: MetricName;
  value: number;
  unit: Unit;
  low_reliability?: boolean;
}

export interface HzRange {
  min: number;
  max: number;
}

/** 측정 블록에서 기기가 계산한 기준값. 구하지 못한 값은 null */
export interface Baselines {
  sessionId: string;
  measuredAt: string;
  /** 잡음 바닥(dBFS) */
  noiseFloorDbfs: number | null;
  /** "아" 음량(dBFS) */
  sustainedLevelDbfs: number | null;
  /** 평가 문장의 기본주파수 중앙값(Hz) */
  f0MedianHz: number | null;
  /** 최대발성지속시간(초) */
  maxPhonationTimeS: number | null;
  /** 평가 문장의 조음속도(음절/초) = N(정답 음절 수) ÷ T_speech(휴지를 뺀 발화 시간, 초). 기기는 발화를 음량으로 나눈다 */
  articulationRateSps: number | null;
  /** 캘리브레이션 글라이드에서 구한 편한 음높이 범위(5·95백분위, Hz). 첫 회차에 구해 저장 */
  f0RangeHz: HzRange | null;
  /** 기본주파수 탐색 범위(Hz). 서버와 같은 규칙(metrics-spec.md 4.6), 캘리브레이션이 없으면 75~500 */
  f0SearchRangeHz: HzRange;
}

/**
 * 기기 쪽 세션 상태
 * - recording: complete 전(녹음·게임 진행 중)
 * - queued: complete를 불렀고 보내기를 기다림(실패 뒤 다시 기다리는 경우 포함)
 * - sending: 등록·업로드·complete 진행 중
 * - processing: 서버가 complete를 받음(202). 서버 계산 중
 * - done, failed: 서버 결과
 * - blocked: 다시 보내도 같은 오류(401, 409 ID 충돌, 413, 422). 개발 확인 필요
 */
export type LocalState = 'recording' | 'queued' | 'sending' | 'processing' | 'done' | 'failed' | 'blocked';

export interface ErrorInfo {
  code: string;
  message: string;
  at: string;
}

export interface ServerMeasurement {
  metric: MetricName;
  value: number;
  unit: Unit;
  low_reliability: boolean;
  algorithm_version: string;
}

/** GET /v1/sessions/{id} 응답(api.md 4.5) */
export interface SessionDetail {
  id: string;
  user_id: string;
  purpose: Purpose;
  started_at: string;
  ended_at: string | null;
  device_model: string;
  os_version: string;
  app_version: string;
  input_device: string;
  sample_rate: number;
  status: ServerStatus;
  is_baseline: boolean;
  server_measurements: ServerMeasurement[];
  error: { code: string; message: string } | null;
}

export interface SessionProgress {
  sessionId: string;
  purpose: Purpose;
  state: LocalState;
  /** 올린 녹음 수 / 올릴 녹음 수 */
  uploaded: number;
  total: number;
  lastError: ErrorInfo | null;
  /** 마지막으로 받은 서버 상태(api.md 4.5). 아직 없으면 null */
  server: SessionDetail | null;
}

export type ChangeStatus = 'no_data' | 'low_reliability' | 'pending_mdc' | 'no_change' | 'increase' | 'decrease';

/** GET /v1/users/{id}/history 응답(api.md 8장) */
export interface History {
  user_id: string;
  metrics: {
    metric: MetricName;
    unit: Unit;
    desired_direction: DesiredDirection;
    baseline: { session_id: string; value: number } | null;
    series: { session_id: string; started_at: string; value: number; low_reliability: boolean }[];
    latest: { value: number; change: number | null } | null;
    mdc: { value: number; scale: 'raw' | 'log' } | null;
    status: ChangeStatus;
  }[];
  recent_device_measurements: {
    session_id: string;
    started_at: string;
    measurements: DeviceMeasurement[];
  }[];
}

export interface UserInfo {
  userId: string;
  createdAt: string;
  /** 개발용 "새 사용자로 시작"으로 만든 사용자. 세션 client_meta에 dev: true가 들어간다 */
  dev: boolean;
}

export interface SessionSummary {
  sessionId: string;
  userId: string;
  purpose: Purpose;
  startedAt: string;
  state: LocalState;
  serverStatus: ServerStatus | null;
  /** 마지막 보내기 오류 코드(예: NETWORK). 없으면 null */
  lastErrorCode: string | null;
  /** 올린 녹음 수 / 올릴 녹음 수 */
  uploaded: number;
  total: number;
}
