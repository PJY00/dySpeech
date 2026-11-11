"""공통 전처리: 모노 → 16 kHz → 60 Hz 고역 통과, 30 ms 프레임(10 ms 간격) 음량(dBFS).

메모리를 아끼려고 표본은 32비트 실수로 다룬다(120초 48 kHz 파일 하나가 64비트로는 중간 배열까지 +150 MB였다).
음량의 제곱합만 64비트로 더해 정밀도를 지킨다.
"""

import io
from dataclasses import dataclass
from math import gcd

import numpy as np
import soundfile
from scipy.signal import butter, resample_poly, sosfiltfilt

RATE = 16000
FRAME = 480  # 30 ms
HOP = 160  # 10 ms
HOP_S = HOP / RATE
HIGHPASS_HZ = 60
EPS = 1e-10  # log 0을 막는 값(−200 dBFS)
_HIGHPASS = butter(4, HIGHPASS_HZ, "highpass", fs=RATE, output="sos").astype(np.float32)


@dataclass(frozen=True)
class Audio:
    samples: np.ndarray  # 16 kHz, −1~1
    levels: np.ndarray  # 프레임 음량(dBFS)

    @property
    def duration_s(self) -> float:
        """
        16 kHz로 바꾼 녹음의 길이를 초로 구함
        입력: 없음 (이 객체의 samples, 16 kHz 표본)
        출력: 녹음 길이(초)
        알고리즘
        1. 표본 수를 RATE(16000 Hz)로 나눔
        """
        return len(self.samples) / RATE

    def frame_times(self) -> np.ndarray:
        """
        프레임마다 중심 시각을 초로 구함
        입력: 없음 (이 객체의 levels, 프레임 음량 목록)
        출력: 프레임 중심 시각 배열(초). 프레임 i의 중심 = (i(프레임 번호) × 160표본 + 480표본 ÷ 2) ÷ 16000 Hz
        알고리즘
        1. 프레임 번호에 간격 HOP(160표본, 10 ms)을 곱하고 프레임 길이 FRAME(480표본, 30 ms)의 절반을 더한 뒤 16000 Hz로 나눔
        """
        return (np.arange(len(self.levels)) * HOP + FRAME / 2) / RATE


def to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """
    녹음을 다상 필터로 16 kHz로 바꿈
    입력: samples (모노 표본, −1~1), rate (입력 샘플레이트, Hz)
    출력: 16 kHz 표본 배열. 입력이 32비트 실수면 필터와 출력도 32비트 실수임(scipy 1.18)
    알고리즘
    1. rate가 16000 Hz면 그대로 돌려줌
    2. 아니면 g(16000과 rate의 최대공약수)로 나눈 비율(올림 16000 ÷ g, 내림 rate ÷ g)로 resample_poly를 실행함
    """
    if rate == RATE:
        return samples
    divisor = gcd(RATE, rate)
    return resample_poly(samples, RATE // divisor, rate // divisor)


def frame_levels(samples: np.ndarray) -> np.ndarray:
    """
    30 ms 프레임(10 ms 간격)마다 음량을 dBFS로 구함
    입력: samples (16 kHz 표본, −1~1, 32비트 실수)
    출력: 프레임 음량 배열(dBFS). 표본이 480개(30 ms)보다 적으면 빈 배열
    알고리즘
    1. 표본이 FRAME(480표본)보다 적으면 빈 배열을 돌려줌
    2. 표본을 HOP(160표본, 10 ms) 묶음으로 자르고(끝에 남는 표본은 버림), 묶음별 제곱합을 64비트 실수로 더함
    3. 프레임 하나(480표본)는 이어진 묶음 3개이므로, 이웃한 묶음 제곱합 3개를 더해 E(프레임 제곱합)를 구함. 프레임 수 = (표본 수 − 480) ÷ 160의 몫 + 1
    4. L(프레임 음량, dBFS) = 20 × log₁₀(RMS(제곱평균제곱근 진폭) + ε(log 0을 막는 값 10⁻¹⁰, −200 dBFS)), RMS(제곱평균제곱근 진폭) = √(E(프레임 제곱합) ÷ 480(프레임 표본 수))
    """
    if len(samples) < FRAME:
        return np.empty(0)
    blocks = samples[: len(samples) // HOP * HOP].reshape(-1, HOP)
    energy = np.add.reduce(blocks * blocks, axis=1, dtype=np.float64)
    count = (len(samples) - FRAME) // HOP + 1
    span = FRAME // HOP
    frame_energy = sum(energy[k : k + count] for k in range(span))
    return 20 * np.log10(np.sqrt(frame_energy / FRAME) + EPS)


def prepare(samples: np.ndarray, rate: int) -> Audio:
    """
    표본을 16 kHz로 바꾸고 60 Hz 고역 통과 필터를 건 뒤 프레임 음량까지 구함
    입력: samples (모노 표본, −1~1), rate (샘플레이트, Hz)
    출력: Audio(16 kHz 32비트 실수 표본, 프레임 음량 dBFS)
    알고리즘
    1. 표본을 32비트 실수로 바꾸고 16 kHz로 바꿈(to_16k)
    2. 표본이 27개보다 많으면 60 Hz 4차 버터워스 고역 통과 필터를 앞뒤로 두 번 걸어 위상 변화 없이 거름. sosfiltfilt가 앞뒤로 덧대는 길이보다 길어야 해서 짧은 표본은 거르지 않음
    3. 프레임 음량을 구해(frame_levels) 표본과 함께 돌려줌
    """
    x = to_16k(np.asarray(samples, dtype=np.float32), rate)
    if len(x) > 27:  # sosfiltfilt가 앞뒤로 덧대는 길이보다 길어야 한다
        x = sosfiltfilt(_HIGHPASS, x).astype(np.float32, copy=False)
    return Audio(x, frame_levels(x))


def load_wav(data: bytes) -> Audio:
    """
    WAV 바이트를 읽어 모노 16 kHz 전처리까지 마침
    입력: data (WAV 파일 바이트)
    출력: Audio(16 kHz 표본, 프레임 음량 dBFS)
    알고리즘
    1. soundfile로 32비트 실수, 표본 × 채널 2차원 배열로 읽음
    2. 채널이 하나면 그대로, 여럿이면 채널 평균으로 모노를 만들고, 읽은 배열은 바로 버림(메모리 절약)
    3. prepare로 16 kHz 변환, 고역 통과 필터, 프레임 음량 계산을 함
    """
    samples, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    mono = samples[:, 0] if samples.shape[1] == 1 else samples.mean(axis=1, dtype=np.float32)
    del samples
    return prepare(mono, rate)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """
    참인 프레임이 이어진 구간을 [시작, 끝) 목록으로 구함
    입력: mask (프레임마다 참·거짓)
    출력: (시작 프레임, 끝 프레임) 목록. 끝은 포함하지 않음
    알고리즘
    1. 앞뒤에 거짓을 하나씩 덧붙이고 값이 바뀌는 위치를 찾음
    2. 바뀌는 위치를 둘씩 짝지어 (시작, 끝)으로 돌려줌
    """
    padded = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def bridge(segments: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """
    사이가 max_gap 프레임 이하인 구간을 이어 붙임
    입력: segments (시작 순으로 놓인 (시작, 끝) 프레임 구간 목록), max_gap (이어 붙일 최대 간격, 프레임 수. 1프레임 = 10 ms)
    출력: 이어 붙인 구간 목록
    알고리즘
    1. 구간을 차례로 보며, 앞 구간 끝과 이 구간 시작의 차가 max_gap 이하면 앞 구간의 끝을 이 구간 끝으로 늘림
    2. 아니면 새 구간으로 추가함
    """
    merged: list[tuple[int, int]] = []
    for start, end in segments:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def frames(seconds: float) -> int:
    """
    초를 프레임 수(10 ms 간격)로 바꿈
    입력: seconds (시간, 초)
    출력: 프레임 수(정수)
    알고리즘
    1. seconds ÷ HOP_S(프레임 간격 0.01초)를 반올림함
    """
    return round(seconds / HOP_S)
