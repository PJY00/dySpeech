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
        """
        발성 구간 길이를 초로 구함
        입력: 없음 (이 객체의 start·end, 프레임 번호)
        출력: 발성 구간 길이(초). 이어 붙인 끊김도 길이에 들어감
        알고리즘
        1. (end − start) 프레임 수에 프레임 간격 0.01초를 곱함
        """
        return (self.end - self.start) * HOP_S

    @property
    def median_level(self) -> float:
        """
        "아" 음량을 발성 프레임 음량의 중앙값으로 구함
        입력: 없음 (이 객체의 levels·voiced)
        출력: "아" 음량(dBFS)
        알고리즘
        1. 구간 안에서 실제로 발성인 프레임(voiced. 이어 붙인 끊김은 뺌)의 음량 중앙값을 구함
        """
        return float(np.median(self.levels[self.voiced]))


def longest_phonation(audio: Audio, noise_floor: float) -> Phonation | None:
    """
    발성 구간 중 가장 긴 것을 찾음
    입력: audio (전처리한 녹음, 프레임 음량 dBFS), noise_floor (잡음 바닥, dBFS)
    출력: 가장 긴 발성 구간(Phonation: 시작·끝 프레임, 구간의 프레임 음량, 실제 발성 프레임 표시). 발성이 없으면 None
    알고리즘
    1. 프레임 음량 > noise_floor(잡음 바닥) + 10 dB인 프레임을 발성으로 봄. 기본주파수로 판정하지 않음(쉰 목소리도 발성으로 셈)
    2. 발성 프레임이 이어진 구간을 구하고, 0.2초(20프레임) 이하로 끊긴 곳은 이어 붙임
    3. 0.1초(10프레임)보다 짧은 구간은 버림
    4. 남은 구간 중 가장 긴 것(길이가 같으면 앞 구간)을 고르고, 그 구간의 음량과 실제 발성 프레임 표시(이어 붙인 끊김은 거짓)를 함께 돌려줌
    """
    above = audio.levels > noise_floor + PHONATION_MARGIN_DB
    segments = bridge(runs(above), frames(BRIDGE_S))
    segments = [(s, e) for s, e in segments if e - s >= frames(MIN_PIECE_S)]
    if not segments:
        return None
    start, end = max(segments, key=lambda seg: seg[1] - seg[0])
    return Phonation(start, end, audio.levels[start:end], above[start:end])


def decay_slope(phonation: Phonation) -> float | None:
    """
    가장 긴 발성 구간의 음량 감쇠 기울기를 구함
    입력: phonation (가장 긴 발성 구간)
    출력: 음량 감쇠 기울기 b(dB/초). 쓸 프레임이 2개보다 적으면 None
    알고리즘
    1. 구간 앞뒤에서 0.3초(30프레임)씩 뺀 프레임만 남김
    2. 그중 이어 붙인 끊김의 프레임(실제 발성이 아님)은 뺌
    3. 남은 프레임이 2개보다 적으면 None을 돌려줌
    4. t(구간 시작부터의 프레임 시각, 초)와 L(프레임 음량, dBFS)에 최소제곱 직선 L(t) = a(절편, dB) + b(기울기, dB/초) × t를 맞추고 b를 돌려줌
    """
    trim = frames(SLOPE_TRIM_S)
    index = np.arange(trim, len(phonation.levels) - trim)
    index = index[phonation.voiced[index]] if len(index) else index
    if len(index) < 2:
        return None
    slope, _ = np.polyfit(index * HOP_S, phonation.levels[index], 1)
    return float(slope)
