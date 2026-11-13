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
        """
        전체 낭독 시간 T_total을 초로 구함
        입력: 없음 (이 객체의 start·end, 프레임 번호)
        출력: T_total(전체 낭독 시간, 초) = 마지막 발화 끝 − 첫 발화 시작
        알고리즘
        1. (end − start) 프레임 수에 프레임 간격 0.01초를 곱함
        """
        return (self.end - self.start) * HOP_S

    @property
    def pause_s(self) -> float:
        """
        휴지 시간 합 T_pause를 초로 구함
        입력: 없음 (이 객체의 segments, 휴지로 나뉜 발화 구간)
        출력: T_pause(휴지 시간 합, 초): 첫 발화 시작과 마지막 발화 끝 사이 0.25초 이상 휴지의 합
        알고리즘
        1. 이웃한 발화 구간마다 (다음 구간 시작 − 앞 구간 끝) 프레임 수를 더하고 0.01초를 곱함. 구간 사이에는 find_reading에서 이미 0.25초 이상 공백만 남아 있음
        """
        return sum(b[0] - a[1] for a, b in zip(self.segments, self.segments[1:])) * HOP_S

    @property
    def speech_s(self) -> float:
        """
        발화 시간 T_speech를 초로 구함
        입력: 없음
        출력: T_speech(발화 시간, 초) = T_total(전체 낭독 시간, 초) − T_pause(휴지 시간 합, 초)
        알고리즘
        1. total_s에서 pause_s를 뺌
        """
        return self.total_s - self.pause_s

    def mask(self, length: int) -> np.ndarray:
        """
        발화 구간 프레임만 참인 배열을 돌려줌
        입력: length (배열 길이, 프레임 수)
        출력: 길이 length의 참·거짓 배열(발화 구간 프레임만 참, 휴지는 거짓)
        알고리즘
        1. 모두 거짓인 배열에서 발화 구간마다 [시작, 끝) 프레임을 참으로 바꿈
        """
        mask = np.zeros(length, dtype=bool)
        for start, end in self.segments:
            mask[start:end] = True
        return mask


def speech_frames(audio: Audio, noise_floor: float, probabilities: np.ndarray) -> np.ndarray:
    """
    10 ms 프레임마다 발화 프레임인지 판정함
    입력: audio (전처리한 녹음), noise_floor (잡음 바닥, dBFS), probabilities (발화 확률, 32 ms(512표본) 덩어리마다 하나)
    출력: 프레임별 참·거짓 배열. 발화 확률 ≥ 0.5 이고 프레임 음량 > 잡음 바닥 + 10 dB이면 참
    알고리즘
    1. 프레임 중심 시각 × 16000 Hz ÷ 512표본의 몫으로, 그 중심이 들어 있는 32 ms 덩어리 번호를 구하고 마지막 덩어리 번호를 넘지 않게 자름
    2. 그 덩어리의 발화 확률이 0.5 이상인지 봄. 확률이 하나도 없으면 모두 거짓
    3. 2단계가 참이고 프레임 음량 > 잡음 바닥 + 10 dB인 프레임만 참으로 둠
    """
    chunk_index = np.minimum((audio.frame_times() * RATE / CHUNK).astype(int), len(probabilities) - 1)
    likely = probabilities[chunk_index] >= SPEECH_PROBABILITY if len(probabilities) else np.zeros(len(audio.levels), bool)
    return likely & (audio.levels > noise_floor + SPEECH_MARGIN_DB)


def find_reading(audio: Audio, noise_floor: float, probabilities: np.ndarray) -> Reading | None:
    """
    평가 문장 녹음에서 발화 구간과 휴지를 나눔
    입력: audio (전처리한 평가 문장 녹음), noise_floor (잡음 바닥, dBFS), probabilities (발화 확률, 32 ms 덩어리마다 하나)
    출력: Reading(첫 발화 시작 프레임, 마지막 발화 끝 프레임(포함하지 않음), 휴지로 나뉜 발화 구간 목록). 발화 프레임이 없으면 None
    알고리즘
    1. 발화 프레임(speech_frames)이 이어진 구간을 구함
    2. 0.25초보다 짧은 공백(24프레임 이하)은 휴지가 아니므로 이어 붙임. 그래서 남은 구간 사이는 모두 0.25초 이상 휴지임
    3. 구간이 없으면 None, 있으면 첫 구간 시작·마지막 구간 끝과 구간 목록을 돌려줌
    """
    # 0.25초보다 짧은 공백은 휴지가 아니므로 잇는다. 그래서 남은 구간 사이는 모두 휴지다.
    segments = bridge(runs(speech_frames(audio, noise_floor, probabilities)), frames(PAUSE_S) - 1)
    if not segments:
        return None
    return Reading(segments[0][0], segments[-1][1], segments)


def rates(reading: Reading, syllables: int) -> dict[str, float]:
    """
    말속도 지표 세 가지를 구함
    입력: reading (발화·휴지 구분 결과), syllables (평가 문장의 정답 음절 수)
    출력: {"articulation_rate": 조음속도(음절/초), "speech_rate": 전체 말속도(음절/초), "pause_ratio": 무음 비율(%)}
    알고리즘
    1. AR(조음속도, 음절/초) = N(음절 수) ÷ T_speech(발화 시간, 초)
    2. SR(전체 말속도, 음절/초) = N(음절 수) ÷ T_total(전체 낭독 시간, 초)
    3. P(무음 비율, %) = T_pause(휴지 시간 합, 초) ÷ T_total(전체 낭독 시간, 초) × 100
    """
    return {
        "articulation_rate": syllables / reading.speech_s,
        "speech_rate": syllables / reading.total_s,
        "pause_ratio": reading.pause_s / reading.total_s * 100,
    }
