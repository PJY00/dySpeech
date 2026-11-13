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
    """
    캘리브레이션으로 기본주파수 탐색 범위를 정함
    입력: calibration (편한 음높이 범위 (최저, 최고), Hz. 없으면 None)
    출력: 기본주파수 탐색 범위 (아래쪽, 위쪽), Hz
    알고리즘
    1. 캘리브레이션이 없으면 기본 범위 75~500 Hz를 돌려줌
    2. 아래쪽 = 캘리브레이션 최저 × 0.8과 75 Hz 중 낮은 쪽, 다만 50 Hz 이상. 아래쪽은 기본값(75 Hz)보다 올리지 않음. 말할 때의 음높이가 글라이드의 가장 낮은 소리보다 낮을 수 있어서임(2026-11-07 실제 녹음: 글라이드 최저 116 Hz, 낭독 중앙값 100 Hz)
    3. 위쪽 = 캘리브레이션 최고 × 1.2, 다만 800 Hz 이하. 위쪽을 좁혀 옥타브 위 오류를 줄임
    4. 위쪽이 아래쪽의 2배보다 크면 그 범위를, 아니면 기본 범위 75~500 Hz를 돌려줌
    """
    if calibration is None:
        return DEFAULT_RANGE_HZ
    low = max(min(calibration[0] * CALIBRATION_WIDEN[0], DEFAULT_RANGE_HZ[0]), LIMIT_HZ[0])
    high = min(calibration[1] * CALIBRATION_WIDEN[1], LIMIT_HZ[1])
    return (low, high) if high > 2 * low else DEFAULT_RANGE_HZ


def extract(samples: np.ndarray, floor: float, ceiling: float) -> F0Track:
    """
    Praat 자기상관 방식으로 10 ms마다 기본주파수를 구함
    입력: samples (16 kHz 표본, −1~1), floor (탐색 범위 아래쪽, Hz), ceiling (탐색 범위 위쪽, Hz)
    출력: F0Track(프레임 중심 시각 배열(초), 기본주파수 배열(Hz, 무성 프레임은 nan))
    알고리즘
    1. 표본으로 parselmouth Sound(16000 Hz)를 만들고 To Pitch (ac)를 시간 간격 0.01초, 아래쪽 floor, 위쪽 ceiling으로 실행함(나머지 설정은 parselmouth 기본값)
    2. 고른 후보의 주파수를 꺼내 0 이하(무성)는 nan으로 바꿈
    3. 프레임 시각과 함께 돌려줌
    """
    pitch = parselmouth.Sound(samples, sampling_frequency=RATE).to_pitch_ac(
        time_step=TIME_STEP_S, pitch_floor=floor, pitch_ceiling=ceiling
    )
    hz = pitch.selected_array["frequency"].astype(float)
    hz[hz <= 0] = np.nan
    return F0Track(np.asarray(pitch.xs()), hz)


def semitones(hz: np.ndarray, reference: float) -> np.ndarray:
    """
    기본주파수를 기준 주파수에 대한 반음 값으로 바꿈
    입력: hz (기본주파수, Hz. 배열 또는 숫자), reference (기준 기본주파수, Hz)
    출력: 반음 값(반음)
    알고리즘
    1. st(반음 값) = 12 × log₂(F(기본주파수, Hz) ÷ F_ref(기준 기본주파수, Hz))
    """
    return 12 * np.log2(hz / reference)


def clean(hz: np.ndarray) -> np.ndarray:
    """
    유성 구간 앞뒤를 버리고 옥타브 오류를 접은 뒤 5프레임 중앙값 필터로 기본주파수를 다듬음
    입력: hz (프레임별 기본주파수, Hz. 무성은 nan)
    출력: 보정한 기본주파수 배열(Hz. 무성·버린 값은 nan). 입력 배열은 바꾸지 않음
    알고리즘
    1. 유성 프레임이 이어진 구간마다 앞뒤 2프레임(20 ms)을 nan으로 버림
    2. 남은 유성 프레임이 없으면 그대로 돌려줌
    3. 남은 유성 프레임의 중앙값을 구하고, 중앙값에서 10반음 이상 벗어난 값은 2배와 1/2 중 중앙값에 가까운 쪽으로 접음. 접어도 10반음 이상이면 nan으로 버림
    4. 다시 구한 유성 구간마다 5프레임 중앙값 필터(구간 끝은 가장 가까운 값으로 채움)를 걸어 돌려줌
    """
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
        """
        억양 값이 신뢰 낮음인지 판정함
        입력: 없음 (이 객체의 voiced_ratio, 발화 프레임 중 유성 프레임 비율)
        출력: 유성 프레임 비율이 0.5(50%) 미만이면 참
        알고리즘
        1. voiced_ratio < 0.5인지 봄. 옥타브 오류가 남으면 표준편차가 커져 억양이 좋아진 것처럼 보이므로, 유성이 적으면 믿지 않음
        """
        # 옥타브 오류가 남으면 표준편차가 커져 억양이 좋아진 것처럼 보이므로, 유성이 적으면 믿지 않는다.
        return self.voiced_ratio < MIN_VOICED_RATIO


def intonation(track: F0Track, speech_mask_at) -> Intonation | None:
    """
    평가 문장 발화 구간의 기본주파수로 억양 지표와 기본주파수 중앙값을 구함
    입력: track (기본주파수 추출 결과: 프레임 시각(초)과 기본주파수(Hz)), speech_mask_at (시각 배열(초)을 받아 그 시각이 발화 구간인지 참·거짓 배열을 돌려주는 함수)
    출력: Intonation(f0_sd_semitones 기본주파수 반음 표준편차(반음), pitch_range_semitones 음도 범위(반음), f0_median 기본주파수 중앙값(Hz), voiced_ratio 발화 프레임 중 유성 프레임 비율(0~1)). 발화 프레임이 없거나 유성 값이 2개보다 적으면 None
    알고리즘
    1. 기본주파수 프레임 시각마다 발화 구간인지 구하고, 발화 프레임이 없으면 None을 돌려줌
    2. 기본주파수를 보정(clean)한 뒤 발화 구간 안의 유성 프레임 값만 남김. 2개보다 적으면 None을 돌려줌
    3. 남은 값의 중앙값을 f0_median으로 하고, 그 중앙값을 기준으로 반음 값으로 바꿈
    4. f0_sd_semitones = 반음 값의 표준편차(모표준편차, 자유도 보정 없음)
    5. pitch_range_semitones = 반음 값의 95백분위 − 5백분위
    6. voiced_ratio = 남은 유성 값 수 ÷ 발화 프레임 수
    """
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
    """
    캘리브레이션 글라이드에서 편한 음높이 범위를 구함
    입력: samples (calibration_glide 녹음의 16 kHz 표본, −1~1)
    출력: 편한 음높이 범위 (5백분위, 95백분위), Hz. 보정한 유성 값이 50프레임(0.5초)보다 적으면 None
    알고리즘
    1. 기본 범위 75~500 Hz로 기본주파수를 구하고(extract) 보정함(clean)
    2. 유성 값이 50프레임(0.5초)보다 적으면 None을 돌려줌
    3. 유성 값의 5백분위와 95백분위를 돌려줌
    """
    f0 = clean(extract(samples, *DEFAULT_RANGE_HZ).hz)
    values = f0[~np.isnan(f0)]
    if len(values) < MIN_CALIBRATION_FRAMES:
        return None
    low, high = np.percentile(values, CALIBRATION_PERCENTILES)
    return float(low), float(high)
