"""평가 문장 낭독: 발화·휴지 구분과 말속도 지표.

발화 프레임 = 발화 확률 ≥ 0.5 이고 음량 > 잡음 바닥 + 10 dB.
공백이 0.25초 이상이면 휴지다. 된소리·거센소리의 막힘(0.1~0.2초)을 휴지로 세지 않기 위해서다.
"""

from dataclasses import dataclass

import numpy as np

from app.metrics.audio import HOP_S, RATE, Audio, bridge, frames, runs
from app.metrics.vad import CHUNK

SPEECH_PROBABILITY = 0.5
SPEECH_MARGIN_DB = 10
PAUSE_S = 0.25


@dataclass(frozen=True)
class Reading:
    start: int  # 첫 발화 시작 프레임
    end: int  # 마지막 발화 끝 프레임(포함하지 않음)
    segments: list[tuple[int, int]]  # 휴지로 나뉜 발화 구간

    @property
    def total_s(self) -> float:
        """T_total: 마지막 발화 끝 − 첫 발화 시작"""
        return (self.end - self.start) * HOP_S

    @property
    def pause_s(self) -> float:
        """T_pause: 그 사이 0.25초 이상 휴지의 합"""
        return sum(b[0] - a[1] for a, b in zip(self.segments, self.segments[1:])) * HOP_S

    @property
    def speech_s(self) -> float:
        return self.total_s - self.pause_s

    def mask(self, length: int) -> np.ndarray:
        mask = np.zeros(length, dtype=bool)
        for start, end in self.segments:
            mask[start:end] = True
        return mask


def speech_frames(audio: Audio, noise_floor: float, probabilities: np.ndarray) -> np.ndarray:
    """10 ms 프레임마다, 그 중심이 들어 있는 32 ms 덩어리의 발화 확률로 판정한다."""
    chunk_index = np.minimum((audio.frame_times() * RATE / CHUNK).astype(int), len(probabilities) - 1)
    likely = probabilities[chunk_index] >= SPEECH_PROBABILITY if len(probabilities) else np.zeros(len(audio.levels), bool)
    return likely & (audio.levels > noise_floor + SPEECH_MARGIN_DB)


def find_reading(audio: Audio, noise_floor: float, probabilities: np.ndarray) -> Reading | None:
    # 0.25초보다 짧은 공백은 휴지가 아니므로 잇는다. 그래서 남은 구간 사이는 모두 휴지다.
    segments = bridge(runs(speech_frames(audio, noise_floor, probabilities)), frames(PAUSE_S) - 1)
    if not segments:
        return None
    return Reading(segments[0][0], segments[-1][1], segments)


def rates(reading: Reading, syllables: int) -> dict[str, float]:
    """AR = N ÷ T_speech, SR = N ÷ T_total, P = T_pause ÷ T_total × 100"""
    return {
        "articulation_rate": syllables / reading.speech_s,
        "speech_rate": syllables / reading.total_s,
        "pause_ratio": reading.pause_s / reading.total_s * 100,
    }
