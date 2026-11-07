"""기본주파수(parselmouth 자기상관, 10 ms 간격)와 보정, 억양 지표, 캘리브레이션 음높이 범위."""

from dataclasses import dataclass

import numpy as np
import parselmouth
from scipy.ndimage import median_filter

from app.metrics.audio import RATE, runs

TIME_STEP_S = 0.01
DEFAULT_RANGE_HZ = (75.0, 500.0)  # 캘리브레이션이 없을 때 탐색 범위
CALIBRATION_WIDEN = (0.8, 1.2)  # 편한 음높이 최저 × 0.8 ~ 최고 × 1.2
LIMIT_HZ = (50.0, 800.0)  # 탐색 범위가 넘지 않게 하는 한계
EDGE_FRAMES = 2  # 유성 구간 앞뒤에서 버리는 프레임(20 ms)
OCTAVE_ERROR_ST = 10  # 중앙값에서 이만큼 벗어나면 옥타브 오류로 본다
MEDIAN_FRAMES = 5
MIN_VOICED_RATIO = 0.5  # 발화 프레임 중 유성 프레임이 이보다 적으면 신뢰 낮음
CALIBRATION_PERCENTILES = (5, 95)
MIN_CALIBRATION_FRAMES = 50  # 0.5초


@dataclass(frozen=True)
class F0Track:
    times: np.ndarray  # 프레임 중심 시각(초)
    hz: np.ndarray  # 무성 프레임은 nan


def search_range(calibration: tuple[float, float] | None) -> tuple[float, float]:
    """캘리브레이션으로 위쪽을 좁혀 옥타브 위 오류를 줄인다. 아래쪽은 기본값(75 Hz)보다 올리지 않는다.

    말할 때의 음높이는 글라이드의 가장 낮은 소리보다 낮을 수 있어서다
    (2026-11-07 실제 녹음: 글라이드 최저 116 Hz, 낭독 중앙값 100 Hz).
    """
    if calibration is None:
        return DEFAULT_RANGE_HZ
    low = max(min(calibration[0] * CALIBRATION_WIDEN[0], DEFAULT_RANGE_HZ[0]), LIMIT_HZ[0])
    high = min(calibration[1] * CALIBRATION_WIDEN[1], LIMIT_HZ[1])
    return (low, high) if high > 2 * low else DEFAULT_RANGE_HZ


def extract(samples: np.ndarray, floor: float, ceiling: float) -> F0Track:
    pitch = parselmouth.Sound(samples, sampling_frequency=RATE).to_pitch_ac(
        time_step=TIME_STEP_S, pitch_floor=floor, pitch_ceiling=ceiling
    )
    hz = pitch.selected_array["frequency"].astype(float)
    hz[hz <= 0] = np.nan
    return F0Track(np.asarray(pitch.xs()), hz)


def semitones(hz: np.ndarray, reference: float) -> np.ndarray:
    """st = 12 × log2(F ÷ F_ref)"""
    return 12 * np.log2(hz / reference)


def clean(hz: np.ndarray) -> np.ndarray:
    """1) 유성 구간 앞뒤 20 ms 버리기 2) 옥타브 오류 접기 3) 5프레임 중앙값 필터"""
    f0 = hz.copy()
    for start, end in runs(~np.isnan(f0)):
        f0[start : min(start + EDGE_FRAMES, end)] = np.nan
        f0[max(end - EDGE_FRAMES, start) : end] = np.nan
    voiced = ~np.isnan(f0)
    if not voiced.any():
        return f0
    median = np.median(f0[voiced])
    for index in np.flatnonzero(voiced):
        if abs(semitones(f0[index], median)) < OCTAVE_ERROR_ST:
            continue
        # 2배나 1/2로 접어 중앙값 근처로 돌아오면 고치고, 아니면 버린다.
        folded = min((f0[index] * 2, f0[index] / 2), key=lambda value: abs(semitones(value, median)))
        f0[index] = folded if abs(semitones(folded, median)) < OCTAVE_ERROR_ST else np.nan
    for start, end in runs(~np.isnan(f0)):
        f0[start:end] = median_filter(f0[start:end], size=MEDIAN_FRAMES, mode="nearest")
    return f0


@dataclass(frozen=True)
class Intonation:
    f0_sd_semitones: float
    pitch_range_semitones: float
    f0_median: float
    voiced_ratio: float

    @property
    def low_reliability(self) -> bool:
        # 옥타브 오류가 남으면 표준편차가 커져 억양이 좋아진 것처럼 보이므로, 유성이 적으면 믿지 않는다.
        return self.voiced_ratio < MIN_VOICED_RATIO


def intonation(track: F0Track, speech_mask_at) -> Intonation | None:
    """speech_mask_at(times) → 그 시각이 발화 구간인지. 발화 구간 안의 유성 프레임만 쓴다."""
    in_speech = speech_mask_at(track.times)
    if not in_speech.any():
        return None
    f0 = clean(track.hz)
    values = f0[in_speech & ~np.isnan(f0)]
    if len(values) < 2:
        return None
    median = float(np.median(values))
    st = semitones(values, median)
    low, high = np.percentile(st, [5, 95])
    return Intonation(
        f0_sd_semitones=float(np.std(st)),
        pitch_range_semitones=float(high - low),
        f0_median=median,
        voiced_ratio=len(values) / int(in_speech.sum()),
    )


def calibration_range(samples: np.ndarray) -> tuple[float, float] | None:
    """캘리브레이션 글라이드의 편한 음높이 범위: 보정한 기본주파수의 5·95백분위"""
    f0 = clean(extract(samples, *DEFAULT_RANGE_HZ).hz)
    values = f0[~np.isnan(f0)]
    if len(values) < MIN_CALIBRATION_FRAMES:
        return None
    low, high = np.percentile(values, CALIBRATION_PERCENTILES)
    return float(low), float(high)
