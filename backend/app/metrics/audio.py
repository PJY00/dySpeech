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
        return len(self.samples) / RATE

    def frame_times(self) -> np.ndarray:
        """프레임 중심 시각(초)"""
        return (np.arange(len(self.levels)) * HOP + FRAME / 2) / RATE


def to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """다상 필터로 16 kHz로 바꾼다. 입력이 32비트면 필터와 출력도 32비트다(scipy 1.18)."""
    if rate == RATE:
        return samples
    divisor = gcd(RATE, rate)
    return resample_poly(samples, RATE // divisor, rate // divisor)


def frame_levels(samples: np.ndarray) -> np.ndarray:
    """L = 20·log10(RMS + ε). 프레임(480표본)은 10 ms 묶음(160표본) 3개라, 묶음별 제곱합을 더해 구한다."""
    if len(samples) < FRAME:
        return np.empty(0)
    blocks = samples[: len(samples) // HOP * HOP].reshape(-1, HOP)
    energy = np.add.reduce(blocks * blocks, axis=1, dtype=np.float64)
    count = (len(samples) - FRAME) // HOP + 1
    span = FRAME // HOP
    frame_energy = sum(energy[k : k + count] for k in range(span))
    return 20 * np.log10(np.sqrt(frame_energy / FRAME) + EPS)


def prepare(samples: np.ndarray, rate: int) -> Audio:
    x = to_16k(np.asarray(samples, dtype=np.float32), rate)
    if len(x) > 27:  # sosfiltfilt가 앞뒤로 덧대는 길이보다 길어야 한다
        x = sosfiltfilt(_HIGHPASS, x).astype(np.float32, copy=False)
    return Audio(x, frame_levels(x))


def load_wav(data: bytes) -> Audio:
    samples, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    mono = samples[:, 0] if samples.shape[1] == 1 else samples.mean(axis=1, dtype=np.float32)
    del samples
    return prepare(mono, rate)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """참인 프레임이 이어진 구간 [시작, 끝) 목록"""
    padded = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def bridge(segments: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """사이가 max_gap 프레임 이하인 구간을 잇는다."""
    merged: list[tuple[int, int]] = []
    for start, end in segments:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def frames(seconds: float) -> int:
    return round(seconds / HOP_S)
