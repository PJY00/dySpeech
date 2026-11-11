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
    """
    평가 문장 녹음에서 음절 핵(음량 정점) 수를 셈
    입력: samples (16 kHz 표본, −1~1)
    출력: 검출 음절 수(개). 보조 확인용이며 말속도 계산에는 쓰지 않음
    알고리즘
    1. de Jong과 Wempe의 Praat 스크립트 v2(2010)와 같은 방법·기본값을 씀. 표본으로 Praat Sound(16000 Hz)를 만들고 음량 곡선(To Intensity: 최저 음높이 50 Hz, 시간 간격 자동, 평균 빼기)을 구함
    2. 음량 곡선의 최소·최대(포물선 보간)와 99백분위를 구하고, 정점 문턱 = max(99백분위 − 25 dB, 최소 음량), 무음 구분 문턱 = −25 dB − (최대 − 99백분위)로 정함
    3. 무음 구분(To TextGrid (silences): 위 무음 구분 문턱, 최소 휴지 0.3초, 최소 소리 구간 0.1초)으로 "silent"·"sounding" 구간을 나눔
    4. 음량 곡선의 극댓값(To PointProcess (extrema), Sinc70 보간)을 찾고, 그 시각의 음량(Cubic 보간)이 정점 문턱보다 큰 정점만 남김
    5. 이웃한 두 정점 사이의 최소 음량(골)과 앞 정점 음량의 차가 2 dB보다 크면 앞 정점을 남김. 마지막 정점은 세지 않음(원 스크립트와 같음)
    6. 기본주파수를 구함(To Pitch (ac): 시간 간격 0.02초, 범위 30~450 Hz, 후보 4개, 매우 정확 끔, 무음 문턱 0.03, 유성 문턱 0.25, 옥타브 비용 0.01, 옥타브 도약 비용 0.35, 유성·무성 전환 비용 0.25)
    7. 남은 정점 중 그 시각에 기본주파수가 있고 무음 구분에서 "sounding" 구간인 것만 셈
    """
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
    """
    검출 음절 수가 정답과 크게 달라 읽기 오류일 수 있는지 판정함
    입력: detected (검출 음절 수), expected (정답 음절 수)
    출력: |D(검출 음절 수) − N(정답 음절 수)| ≥ 0.3 × N(정답 음절 수)이면 참
    알고리즘
    1. 두 값의 차의 절댓값이 정답 음절 수의 30%(READING_ERROR_RATIO = 0.3) 이상인지 봄. 검출 음절 수 오차율을 확인할 때까지 이 판정은 쓰지 않음
    """
    return abs(detected - expected) >= READING_ERROR_RATIO * expected
