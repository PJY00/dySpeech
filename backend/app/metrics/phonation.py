"""지속 발성("아"): 최대발성지속시간, "아" 음량, 음량 감쇠 기울기."""

from dataclasses import dataclass

import numpy as np

from app.metrics.audio import HOP_S, Audio, bridge, frames, runs

PHONATION_MARGIN_DB = 10  # 잡음 바닥보다 이만큼 크면 발성
BRIDGE_S = 0.2  # 이 길이 이하로 끊긴 곳은 잇는다
MIN_PIECE_S = 0.1  # 이보다 짧은 조각은 버린다
SLOPE_TRIM_S = 0.3  # 기울기를 맞출 때 앞뒤에서 빼는 길이
SLOPE_MIN_S = 2.0  # 이보다 짧은 발성의 기울기는 신뢰 낮음


@dataclass(frozen=True)
class Phonation:
    start: int  # 프레임 번호
    end: int
    levels: np.ndarray  # 구간의 프레임 음량
    voiced: np.ndarray  # 구간 안에서 실제로 발성인 프레임(이어 붙인 끊김은 거짓)

    @property
    def duration_s(self) -> float:
        return (self.end - self.start) * HOP_S

    @property
    def median_level(self) -> float:
        """"아" 음량: 발성 프레임 음량의 중앙값"""
        return float(np.median(self.levels[self.voiced]))


def longest_phonation(audio: Audio, noise_floor: float) -> Phonation | None:
    """발성(음량 > 잡음 바닥 + 10 dB) 구간 중 가장 긴 것. 기본주파수로 판정하지 않는다(쉰 목소리도 발성으로 센다)."""
    above = audio.levels > noise_floor + PHONATION_MARGIN_DB
    segments = bridge(runs(above), frames(BRIDGE_S))
    segments = [(s, e) for s, e in segments if e - s >= frames(MIN_PIECE_S)]
    if not segments:
        return None
    start, end = max(segments, key=lambda seg: seg[1] - seg[0])
    return Phonation(start, end, audio.levels[start:end], above[start:end])


def decay_slope(phonation: Phonation) -> float | None:
    """앞뒤 0.3초를 뺀 발성 프레임 음량에 맞춘 최소제곱 직선의 기울기(dB/초). 이어 붙인 끊김의 프레임은 넣지 않는다."""
    trim = frames(SLOPE_TRIM_S)
    index = np.arange(trim, len(phonation.levels) - trim)
    index = index[phonation.voiced[index]] if len(index) else index
    if len(index) < 2:
        return None
    slope, _ = np.polyfit(index * HOP_S, phonation.levels[index], 1)
    return float(slope)
