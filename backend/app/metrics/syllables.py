"""검출 음절 수(보조). de Jong & Wempe의 Praat 스크립트 v2(2010)와 같은 방법·기본값.

1. 음량 곡선(To Intensity: 최저 음높이 50 Hz, 평균 빼기)에서 99백분위 − 25 dB 위의 정점만 남긴다.
2. 다음 정점과의 사이에 2 dB보다 깊게 파인 골이 있는 정점만 남긴다(마지막 정점은 세지 않는다. 원 스크립트와 같음).
3. 그 시각에 기본주파수가 있고, 무음 구분(최소 휴지 0.3초)에서 소리 구간인 정점만 센다.
"""

import math

import numpy as np
import parselmouth
from parselmouth.praat import call

from app.metrics.audio import RATE

SILENCE_DB = -25.0
MIN_DIP_DB = 2.0
MIN_PAUSE_S = 0.3
MIN_SOUNDING_S = 0.1
# 정답 음절 수와 이만큼 이상 다르면 "읽기 오류 가능". 4단계에서 검출 음절 수 오차율을 확인할 때까지 쓰지 않는다.
READING_ERROR_RATIO = 0.3


def count_nuclei(samples: np.ndarray) -> int:
    sound = parselmouth.Sound(samples, sampling_frequency=RATE)
    intensity = call(sound, "To Intensity", 50, 0, "yes")
    min_int = call(intensity, "Get minimum", 0, 0, "Parabolic")
    max_int = call(intensity, "Get maximum", 0, 0, "Parabolic")
    max99 = call(intensity, "Get quantile", 0, 0, 0.99)
    threshold = max(max99 + SILENCE_DB, min_int)
    silence_threshold = SILENCE_DB - (max_int - max99)
    textgrid = call(intensity, "To TextGrid (silences)", silence_threshold, MIN_PAUSE_S, MIN_SOUNDING_S, "silent", "sounding")

    contour = call(call(intensity, "Down to Matrix"), "To Sound (slice)", 1)
    extrema = call(contour, "To PointProcess (extrema)", "Left", "yes", "no", "Sinc70")
    peaks = []
    for index in range(1, call(extrema, "Get number of points") + 1):
        time = call(extrema, "Get time from index", index)
        value = call(contour, "Get value at time", time, "Cubic")
        if value > threshold:
            peaks.append((time, value))

    valid = []
    for (time, value), (next_time, _) in zip(peaks, peaks[1:]):
        dip = call(intensity, "Get minimum", time, next_time, "None")
        if abs(value - dip) > MIN_DIP_DB:
            valid.append(time)

    pitch = call(sound, "To Pitch (ac)", 0.02, 30, 4, "no", 0.03, 0.25, 0.01, 0.35, 0.25, 450)
    count = 0
    for time in valid:
        f0 = call(pitch, "Get value at time", time, "Hertz", "Linear")
        interval = call(textgrid, "Get interval at time", 1, time)
        if not math.isnan(f0) and call(textgrid, "Get label of interval", 1, interval) == "sounding":
            count += 1
    return count


def possible_reading_error(detected: int, expected: int) -> bool:
    return abs(detected - expected) >= READING_ERROR_RATIO * expected
