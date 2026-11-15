"""AI Hub 구음장애 음성으로 서버 지표 계산이 환자 음성에서 얼마나 틀리는지 잰다.

정답을 아는 것은 전사(받아 적은 글)뿐이므로, 녹음마다 전사의 음절 수와 서버 코드가 찾은 음절 수를 비교하고,
기본주파수를 얼마나 놓치는지(추적 실패)를 본다. 결과는 서버의 판단 기준(탐색 범위 아래쪽, "읽기 오류 가능", 신뢰 낮음)을
다시 정하는 근거로 쓴다. 환자 개인을 평가하거나 사용자 기록과 섞지 않는다.
backend 폴더에서 실행한다. 음성 zip은 풀지 않고 녹음을 하나씩 읽는다(풀면 167 GB). DB·S3는 쓰지 않는다.
    python -m analysis.aihub run --audio 음성.zip --labels 라벨.zip --out 결과.jsonl
        --group 11      폴더 이름이 이 글자로 시작하는 질환만(예: 11 = 중풍)
        --limit 5       앞에서 5개만(시간을 재 볼 때)
        --workers 4     동시에 계산할 프로세스 수(기본 1)
        이미 결과 파일에 있는 녹음은 건너뛴다(중간에 멈춰도 이어서 돌릴 수 있다).
    python -m analysis.aihub report 결과.jsonl [--json]

계산 방법
- 녹음을 5분씩 잘라 읽어 모노 16 kHz로 맞춘다. 자른 경계에 걸친 발화는 둘로 나뉜다(5분에 한 번).
- 발화 프레임은 서버와 같다(발화 확률 0.5 이상, 음량 > 잡음 바닥 + 10 dB). 잡음 바닥 녹음이 없으므로 서버의 대체 값
  (그 5분 음량의 하위 10백분위)을 쓴다.
- 0.25초 이상 쉬면 휴지, 1초 이상 쉬면 다른 발화 조각으로 본다. 서버 계산은 문장 하나 길이에 맞춰져 있어서
  조각마다 음절 핵 검출과 기본주파수 추출을 따로 한다. 발화가 0.5초보다 짧은 조각은 버린다.
- 음절 수 오차율 = |N_detected(검출 음절 수) − N_expected(전사 음절 수)| ÷ N_expected(전사 음절 수). 녹음 하나 단위다.
  전사에 숫자나 영문이 있으면 음절 수를 셀 수 없어 이 비교에서 뺀다.
- 기본주파수는 서버 기본 범위(75~500 Hz)와 아래쪽을 50 Hz로 내린 범위로 각각 구해, 발화 프레임 중 유성 프레임 비율을 본다.
- 조각을 나누는 1초, 버리는 0.5초, 5분은 모두 초기값이다.
- 요약은 질환별, 과제 종류별(녹음 이름의 과제 번호), 전사 표시 수준별로 나눈다. 앱의 읽기 문장과 같은 종류는 문장 읽기와 문단 읽기다.
  낱말 읽기는 낱말 하나가 0.5초보다 짧은 조각이 되어 많이 버려지므로 검출 음절 수가 적게 나온다.
"""

import argparse
import json
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

from app.metrics import pitch, speech, syllables
from app.metrics.audio import FRAME, HOP, HOP_S, RATE, bridge, frames, prepare, runs, to_16k
from app.metrics.pitch import MIN_VOICED_RATIO
from app.metrics.session import FALLBACK_NOISE_PERCENTILE, frame_at
from app.metrics.syllables import READING_ERROR_RATIO
from app.metrics.vad import get_detector
from app.metrics.version import ALGORITHM_VERSION
from app.storage.wav import WAV_HEADER_BYTES, parse_wav_header
from tools.inspect_session import data_start

BLOCK_S = 300.0  # 한 번에 읽어 계산하는 길이(초)
UTTERANCE_GAP_S = 1.0  # 이만큼 넘게 쉬면 다른 발화 조각으로 본다
MIN_UTTERANCE_SPEECH_S = 0.5  # 발화가 이보다 짧은 조각은 버린다
CLIP_MARGIN_S = 0.3  # 조각 앞뒤에 붙이는 여유(녹음 앞뒤의 조용한 부분처럼)
LOW_FLOOR_HZ = 50.0  # 탐색 범위 아래쪽을 내려 본 값
DEFAULT_FLOOR_HZ, DEFAULT_CEILING_HZ = pitch.DEFAULT_RANGE_HZ
PLAUSIBLE_RATE = (1.0, 10.0)  # 조음속도(음절/초)가 이 밖이면 발화 구간이나 전사가 맞지 않는 것으로 본다

TAG = re.compile(r"(?<![A-Za-z가-힣])[a-z]/")  # n/(잡음), b/(숨), u/(알아듣기 어려움), l/(웃음), o/ 같은 표기
DUAL = re.compile(r"\(([^()]*)\)\s*/\s*\(([^()]*)\)")  # (철자)/(발음) 이중 전사
HANGUL = re.compile(r"[가-힣]")
MU_LAW = None  # 처음 쓸 때 만드는 μ-law 풀이 표(256개)

GROUP_NAMES = {"11": "중풍", "12": "뇌부상", "13": "뇌성마비", "15": "기타 및 복합"}
MARK_LEVELS = ("표시 없음", "표시 적음", "표시 많음")
# 녹음 이름의 과제 번호(여섯째 칸) → 과제 종류. 과제 번호마다 전사를 하나씩 읽어 보고 붙인 이름이다
TASK_KINDS = {"01": "낱말 읽기", "02": "문장 읽기", "03": "자기소개 문장", "04": "문단 읽기", "06": "대화·명령 문장", "07": "긴 글 읽기"}
OTHER_TASK = "그 밖"


@dataclass(frozen=True)
class Transcript:
    syllables: int  # 한글 음절 글자 수
    repeats: int  # + 표기(반복) 수
    unclear: int  # * 표기(불명확 발음) 수
    tags: int  # n/ 같은 표기 수
    countable: bool  # 숫자·영문이 없어 음절 수를 그대로 믿을 수 있는지


def clean_transcript(text: str) -> Transcript:
    """
    AI Hub 전사에서 표기를 걷어 내고 정답 음절 수를 셈
    입력: text (전사 글. 녹음 하나 전체)
    출력: Transcript(한글 음절 수, + 표기 수, * 표기 수, n/ 같은 표기 수, 음절 수를 셀 수 있는지)
    알고리즘
    1. 이중 전사 "(철자)/(발음)"은 실제로 말한 쪽인 발음만 남김
    2. + 와 * 의 개수를 셈. + 로 표시된 반복은 실제로 말한 것이므로 음절은 그대로 셈
    3. 영문 소문자 한 글자와 / 로 된 표기(n/, b/, u/, l/, o/)를 세고 지움
    4. 남은 글에 숫자나 영문이 있으면 countable을 거짓으로 둠(읽는 법에 따라 음절 수가 달라짐)
    5. 한글 음절 글자(가~힣) 수를 셈
    """
    spoken = DUAL.sub(lambda match: match.group(2), text)
    tags = len(TAG.findall(spoken))
    spoken = TAG.sub(" ", spoken)
    countable = re.search(r"[0-9A-Za-z]", spoken) is None
    return Transcript(len(HANGUL.findall(spoken)), spoken.count("+"), spoken.count("*"), tags, countable)


def speaker_of(recording_id: str, sex: str, age: str) -> str:
    """
    녹음 이름과 성별·나이로 화자를 구분하는 이름을 만듦
    입력: recording_id (녹음 이름, 예: ID-01-11-N-AMG-02-02-M-56-KK), sex (라벨의 성별), age (라벨의 나이)
    출력: "머리글자/성별/나이" 문자열. 이름에 머리글자 칸(다섯째)이 없으면 녹음 이름 그대로
    알고리즘
    1. 이름을 - 로 나눠 다섯째 칸(화자 머리글자)을 꺼냄
    2. 머리글자, 성별, 나이를 / 로 이음
    """
    parts = recording_id.split("-")
    return f"{parts[4]}/{sex}/{age}" if len(parts) > 4 else recording_id


def task_kind(recording_id: str) -> str:
    """
    녹음 이름에서 읽은 과제의 종류를 알아냄
    입력: recording_id (녹음 이름, 예: ID-01-11-N-AMG-02-02-M-56-KK)
    출력: 과제 종류 이름(TASK_KINDS의 값). 이름에 과제 번호 칸(여섯째)이 없거나 모르는 번호면 "그 밖"
    알고리즘
    1. 이름을 - 로 나눠 여섯째 칸(과제 번호의 앞 두 자리)을 꺼냄
    2. TASK_KINDS에서 이름을 찾음
    """
    parts = recording_id.split("-")
    return TASK_KINDS.get(parts[5], OTHER_TASK) if len(parts) > 5 else OTHER_TASK


def mu_law_table() -> np.ndarray:
    """
    8비트 μ-law 값을 −1~1 진폭으로 바꾸는 표를 만듦
    입력: 없음
    출력: 길이 256의 32비트 실수 배열(인덱스 = μ-law 바이트 값)
    알고리즘
    1. G.711 μ-law 풀이: 바이트를 뒤집고(~), 부호(최상위 비트)·지수(3비트)·가수(4비트)로 나눔
    2. 크기 = ((가수 << 3) + 132) << 지수 − 132, 부호가 있으면 음수
    3. 32768로 나눠 −1~1로 맞춤. 한 번 만든 표는 다시 씀
    """
    global MU_LAW
    if MU_LAW is None:
        values = ~np.arange(256, dtype=np.uint8)
        exponent = (values >> 4) & 0x07
        mantissa = values & 0x0F
        magnitude = (((mantissa.astype(np.int32) << 3) + 132) << exponent) - 132
        MU_LAW = (np.where(values & 0x80, -magnitude, magnitude) / 32768).astype(np.float32)
    return MU_LAW


def decode(raw: bytes, format_code: int, bits: int, channels: int) -> np.ndarray:
    """
    WAV 소리 데이터 바이트를 모노 −1~1 표본으로 바꿈
    입력: raw (소리 데이터 바이트. 프레임 경계에 맞춘 것), format_code (WAV 형식 코드: 1 = PCM, 3 = 부동소수점, 7 = μ-law), bits (표본당 비트), channels (채널 수)
    출력: 모노 표본 배열(32비트 실수, −1~1). 받지 않는 형식이면 ValueError
    알고리즘
    1. PCM 16비트·32비트는 정수로 읽어 2^(비트 − 1)로 나눔. 24비트는 3바이트씩 부호 있는 정수로 묶어 2^23으로 나눔. 8비트는 부호 없는 값에서 128을 빼고 128로 나눔
    2. 부동소수점 32비트는 그대로, μ-law 8비트는 풀이 표(mu_law_table)로 바꿈
    3. 채널이 여럿이면 채널 평균으로 모노를 만듦
    """
    if format_code == 1 and bits == 16:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    elif format_code == 1 and bits == 32:
        samples = (np.frombuffer(raw, dtype="<i4") / 2147483648).astype(np.float32)
    elif format_code == 1 and bits == 24:
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = data[:, 0] | (data[:, 1] << 8) | (data[:, 2] << 16)
        samples = (np.where(value & 0x800000, value - 0x1000000, value) / 8388608).astype(np.float32)
    elif format_code == 1 and bits == 8:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif format_code == 3 and bits == 32:
        samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif format_code == 7 and bits == 8:
        samples = mu_law_table()[np.frombuffer(raw, dtype=np.uint8)]
    else:
        raise ValueError(f"받지 않는 WAV 형식입니다(형식 코드 {format_code}, {bits}비트)")
    return samples if channels == 1 else samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)


@dataclass(frozen=True)
class WavInfo:
    format_code: int
    channels: int
    sample_rate: int
    bits: int


def read_blocks(stream: BinaryIO, block_s: float = BLOCK_S) -> tuple[WavInfo, Iterator[np.ndarray]]:
    """
    WAV를 앞에서부터 차례로 읽어 일정 길이씩 모노 16 kHz 표본으로 돌려줌. 파일 전체를 메모리에 올리지 않음
    입력: stream (WAV 바이트를 앞에서부터 읽을 수 있는 것. zip 안의 파일도 됨), block_s (한 번에 돌려줄 길이, 초)
    출력: (WavInfo(형식 코드, 채널 수, 샘플레이트 Hz, 표본당 비트), 16 kHz 모노 표본 배열을 차례로 내는 반복자). 머리글을 읽지 못하면 WavHeaderError, 소리 데이터 위치를 못 찾으면 ValueError
    알고리즘
    1. 앞 64 KB를 읽어 형식 정보(parse_wav_header)와 소리 데이터 시작 위치(data_start)를 찾음
    2. 한 프레임 바이트 수 = 채널 수 × 표본당 바이트 수. block_s초 분량의 바이트를 프레임 경계에 맞춰 차례로 읽음
    3. 읽은 덩어리마다 모노 −1~1로 풀고(decode) 16 kHz로 바꿔(prepare의 변환과 같은 다상 필터) 내보냄. 끝에 남은 프레임 조각은 버림
    """
    head = stream.read(WAV_HEADER_BYTES)
    fmt = parse_wav_header(head)
    start = data_start(head)
    if start is None:
        raise ValueError("소리 데이터(data 청크)를 찾지 못했습니다")
    info = WavInfo(fmt.format_code, fmt.channels, fmt.sample_rate, fmt.bits_per_sample)
    frame_bytes = fmt.channels * fmt.bits_per_sample // 8
    block_bytes = int(block_s * fmt.sample_rate) * frame_bytes

    def blocks() -> Iterator[np.ndarray]:
        """
        소리 데이터를 block_s초씩 읽어 16 kHz 모노 표본으로 냄
        입력: 없음 (바깥 함수의 stream, head, 형식 정보)
        출력: 16 kHz 모노 표본 배열(32비트 실수)을 차례로 냄
        알고리즘
        1. 머리글과 함께 이미 읽은 소리 데이터를 먼저 쓰고, block_s초 분량이 찰 때까지(또는 끝까지) stream에서 더 읽음
        2. 프레임 경계에 맞게 잘라 풀고 16 kHz로 바꿔 냄. 더 읽을 것이 없으면 끝냄
        """
        pending = head[start:]
        while True:
            while len(pending) < block_bytes:
                more = stream.read(block_bytes - len(pending))
                if not more:
                    break
                pending += more
            usable = min(len(pending), block_bytes) // frame_bytes * frame_bytes
            if usable == 0:
                return
            chunk, pending = pending[:usable], pending[usable:]
            yield to_16k(decode(chunk, info.format_code, info.bits, info.channels), info.sample_rate).astype(np.float32, copy=False)
            if usable < block_bytes:
                return

    return info, blocks()


@dataclass
class Totals:
    utterances: int = 0
    skipped_short: int = 0
    speech_s: float = 0.0
    pause_s: float = 0.0
    detected_syllables: int = 0
    nuclei_errors: int = 0
    speech_frames: int = 0  # 기본 범위(75~500 Hz) 기본주파수 프레임 중 발화 구간에 든 수
    voiced_frames: int = 0  # 그중 유성으로 잡힌 수
    low_reliability_utterances: int = 0  # 유성 비율이 0.5 미만이거나 유성 구간을 못 찾은 조각
    speech_frames_low_floor: int = 0  # 아래쪽을 50 Hz로 내렸을 때 발화 구간에 든 기본주파수 프레임 수
    voiced_frames_low_floor: int = 0  # 그중 유성으로 잡힌 수
    below_default_floor: int = 0  # 그중 75 Hz 아래 값


def utterances_of(segments: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """
    휴지로 나뉜 발화 구간을 발화 조각으로 묶음
    입력: segments ((시작 프레임, 끝 프레임) 목록. 구간 사이는 0.25초 이상 휴지)
    출력: 조각 목록. 조각 하나는 그 안의 발화 구간 목록
    알고리즘
    1. 앞 구간 끝과 다음 구간 시작 사이가 UTTERANCE_GAP_S(1초)보다 짧으면 같은 조각에 넣고, 아니면 새 조각을 시작함
    """
    groups: list[list[tuple[int, int]]] = []
    limit = frames(UTTERANCE_GAP_S)
    for segment in segments:
        if groups and segment[0] - groups[-1][-1][1] < limit:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return groups


def analyze_block(samples: np.ndarray, detector, totals: Totals, f0_values: list[np.ndarray], low_floor_values: list[np.ndarray]) -> None:
    """
    16 kHz 표본 한 덩어리에서 발화 조각을 찾아 조각마다 서버와 같은 방법으로 계산해 합계에 더함
    입력: samples (16 kHz 모노 표본), detector (발화 검출기), totals (녹음 하나의 합계. 여기에 더함), f0_values (기본 범위로 구한 발화 구간 기본주파수를 모으는 목록), low_floor_values (아래쪽 50 Hz로 구한 값을 모으는 목록)
    출력: 없음 (totals와 두 목록에 더함)
    알고리즘
    1. 60 Hz 고역 통과와 프레임 음량을 구하고(prepare), 발화 확률을 구함
    2. 잡음 바닥 = 이 덩어리 프레임 음량의 하위 10백분위(서버가 잡음 바닥 녹음이 없을 때 쓰는 대체 값)
    3. 발화 프레임(speech_frames)이 이어진 구간을 구하고 0.25초보다 짧은 공백은 이어 붙임(서버 find_reading과 같음)
    4. 구간을 조각으로 묶음(utterances_of). 조각의 발화 시간이 MIN_UTTERANCE_SPEECH_S(0.5초)보다 짧으면 버리고 수만 셈
    5. 조각마다 앞뒤 CLIP_MARGIN_S(0.3초)를 붙여 잘라, 음절 핵 수(count_nuclei)를 세고 발화 시간·휴지 시간을 더함. Praat이 실패하면 수만 셈
    6. 조각의 기본주파수를 기본 범위(75~500 Hz)와 아래쪽 50 Hz 범위로 구해 보정(clean)한 뒤, 발화 프레임 안의 유성 값을 모음
    7. 기본 범위에서 유성 비율(유성 값 수 ÷ 발화 구간에 든 기본주파수 프레임 수)이 0.5 미만이면 신뢰 낮음 조각으로 셈(서버의 신뢰 낮음 기준과 같음)
    8. 50 Hz 범위의 유성 값 중 75 Hz 아래인 것을 셈
    """
    audio = prepare(samples, RATE)
    if len(audio.levels) == 0:
        return
    probabilities = detector.probabilities(audio.samples)
    noise_floor = float(np.percentile(audio.levels, FALLBACK_NOISE_PERCENTILE))
    mask = speech.speech_frames(audio, noise_floor, probabilities)
    segments = bridge(runs(mask), frames(speech.PAUSE_S) - 1)
    margin = frames(CLIP_MARGIN_S)
    for group in utterances_of(segments):
        speech_frames = sum(end - start for start, end in group)
        if speech_frames * HOP_S < MIN_UTTERANCE_SPEECH_S:
            totals.skipped_short += 1
            continue
        first = max(group[0][0] - margin, 0)
        last = min(group[-1][1] + margin, len(audio.levels))
        clip = audio.samples[first * HOP : (last - 1) * HOP + FRAME]
        clip_mask = np.zeros(last - first, dtype=bool)
        for start, end in group:
            clip_mask[start - first : end - first] = True

        totals.utterances += 1
        totals.speech_s += speech_frames * HOP_S
        totals.pause_s += sum(b[0] - a[1] for a, b in zip(group, group[1:])) * HOP_S
        try:
            totals.detected_syllables += syllables.count_nuclei(clip)
        except Exception:  # Praat이 아주 짧거나 조용한 조각에서 실패할 수 있다
            totals.nuclei_errors += 1

        def voiced_in_speech(floor: float) -> tuple[np.ndarray, int]:
            """
            조각의 기본주파수를 구해 발화 구간 안의 유성 값과 발화 구간 프레임 수를 돌려줌
            입력: floor (탐색 범위 아래쪽, Hz)
            출력: (발화 구간 안의 유성 기본주파수 배열(Hz), 발화 구간에 든 기본주파수 프레임 수). 프레임이 없으면 (빈 배열, 0)
            알고리즘
            1. floor~500 Hz 범위로 기본주파수를 구하고(extract) 보정함(clean)
            2. 기본주파수 프레임 시각을 가장 가까운 음량 프레임에 맞춰(frame_at) 발화 구간인 것만 남김. 서버의 억양 계산(intonation)과 같은 방법임
            """
            track = pitch.extract(clip, floor, DEFAULT_CEILING_HZ)
            if len(track.times) == 0:
                return np.empty(0), 0
            f0 = pitch.clean(track.hz)
            in_speech = clip_mask[frame_at(track.times, len(clip_mask))]
            return f0[in_speech & ~np.isnan(f0)], int(in_speech.sum())

        values, in_speech = voiced_in_speech(DEFAULT_FLOOR_HZ)
        totals.speech_frames += in_speech
        totals.voiced_frames += len(values)
        if in_speech == 0 or len(values) / in_speech < MIN_VOICED_RATIO:
            totals.low_reliability_utterances += 1
        f0_values.append(values)
        low_values, low_in_speech = voiced_in_speech(LOW_FLOOR_HZ)
        totals.speech_frames_low_floor += low_in_speech
        totals.voiced_frames_low_floor += len(low_values)
        totals.below_default_floor += int((low_values < DEFAULT_FLOOR_HZ).sum())
        low_floor_values.append(low_values)


def analyze_recording(stream: BinaryIO, detector=None, block_s: float = BLOCK_S) -> dict:
    """
    녹음 하나를 처음부터 끝까지 읽어 발화·음절·기본주파수 합계를 구함
    입력: stream (WAV 바이트를 앞에서부터 읽을 수 있는 것), detector (발화 검출기. 없으면 공용 검출기), block_s (한 번에 읽는 길이, 초)
    출력: 사전. duration_s(녹음 길이, 초), sample_rate(Hz)·channels·bits·format_code(WAV 형식), Totals의 모든 합계, f0_median_hz(기본 범위 유성 값의 중앙값, Hz), f0_p5_hz_low_floor·f0_median_hz_low_floor(아래쪽 50 Hz 범위 유성 값의 5백분위·중앙값, Hz). 유성 값이 없으면 그 값은 None
    알고리즘
    1. read_blocks로 block_s초씩 16 kHz 표본을 받아 analyze_block으로 합계에 더함. 받은 표본 수로 녹음 길이를 구함
    2. 모은 기본주파수 값으로 중앙값과 5백분위를 구함
    """
    detector = detector or get_detector()
    info, blocks = read_blocks(stream, block_s)
    totals = Totals()
    f0_values: list[np.ndarray] = []
    low_values: list[np.ndarray] = []
    count = 0
    for samples in blocks:
        count += len(samples)
        analyze_block(samples, detector, totals, f0_values, low_values)
    default = np.concatenate(f0_values) if f0_values else np.empty(0)
    low = np.concatenate(low_values) if low_values else np.empty(0)
    return {
        "duration_s": round(count / RATE, 2),
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "bits": info.bits,
        "format_code": info.format_code,
        **{key: (round(value, 2) if isinstance(value, float) else value) for key, value in asdict(totals).items()},
        "f0_median_hz": round(float(np.median(default)), 2) if len(default) else None,
        "f0_p5_hz_low_floor": round(float(np.percentile(low, 5)), 2) if len(low) else None,
        "f0_median_hz_low_floor": round(float(np.median(low)), 2) if len(low) else None,
    }


def decode_name(info: zipfile.ZipInfo) -> str:
    """
    zip 안의 파일 이름을 한글로 읽음
    입력: info (zip 항목)
    출력: 파일 이름(경로 포함)
    알고리즘
    1. UTF-8 표시가 있으면 그대로 씀
    2. 없으면 zipfile이 CP437로 읽은 이름을 바이트로 되돌려 UTF-8, 그다음 CP949로 읽어 봄. 둘 다 안 되면 그대로 씀
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


@dataclass(frozen=True)
class Task:
    recording_id: str
    member: str  # 음성 zip 안의 실제 항목 이름(zipfile이 읽은 그대로)
    group: str  # 질환 폴더 이름(예: 11.중풍)
    speaker: str
    sex: str
    age: str
    transcript: Transcript


def load_tasks(audio_zip: Path, labels_zip: Path, group: str | None = None) -> tuple[list[Task], int, int]:
    """
    라벨 zip과 음성 zip에서 이름이 같은 녹음을 짝지어 계산할 목록을 만듦
    입력: audio_zip (음성 zip 경로), labels_zip (라벨 zip 경로), group (질환 폴더 이름이 이 글자로 시작하는 것만, 선택)
    출력: (Task 목록(녹음 이름 순), 음성만 있고 라벨이 없는 수, 라벨만 있고 음성이 없는 수)
    알고리즘
    1. 음성 zip의 .wav 항목을 "녹음 이름 → 항목 이름"으로 모음(녹음 이름 = 확장자를 뗀 파일 이름)
    2. 라벨 zip의 .json을 읽어 전사를 정리하고(clean_transcript), 질환 폴더·성별·나이·화자를 꺼냄
    3. 두 쪽에 모두 있는 녹음만 Task로 만들고, group이 있으면 그 질환만 남김
    """
    with zipfile.ZipFile(audio_zip) as audio:
        members = {
            Path(decode_name(info)).stem: info.filename
            for info in audio.infolist()
            if not info.is_dir() and decode_name(info).lower().endswith(".wav")
        }
    tasks = []
    labelled = set()
    with zipfile.ZipFile(labels_zip) as labels:
        for info in labels.infolist():
            name = decode_name(info)
            if info.is_dir() or not name.lower().endswith(".json"):
                continue
            recording_id = Path(name).stem
            labelled.add(recording_id)
            if recording_id not in members:
                continue
            folder = name.split("/")[0]
            if group is not None and not folder.startswith(group):
                continue
            label = json.loads(labels.read(info).decode("utf-8-sig"))
            patient = label.get("Patient_info") or {}
            sex, age = str(patient.get("Sex") or ""), str(patient.get("Age") or "")
            tasks.append(
                Task(recording_id, members[recording_id], folder, speaker_of(recording_id, sex, age), sex, age, clean_transcript(label.get("Transcript") or ""))
            )
    tasks.sort(key=lambda task: task.recording_id)
    return tasks, len(set(members) - labelled), len(labelled - set(members))


def compute(audio_zip: str, task: Task) -> dict:
    """
    녹음 하나를 계산해 결과 한 줄을 만듦(작업 프로세스에서 실행)
    입력: audio_zip (음성 zip 경로), task (계산할 녹음)
    출력: 결과 사전. id, group, speaker, sex, age, 전사 정보(expected_syllables, countable, repeats, unclear, tags), analyze_recording의 값, seconds(계산 시간, 초), algorithm_version, error(실패하면 "예외 이름: 내용", 아니면 None)
    알고리즘
    1. 음성 zip에서 그 녹음을 열어 analyze_recording으로 계산함
    2. 예외가 나면 error에 적고 나머지 값 없이 돌려줌(다른 녹음은 계속 계산함)
    """
    started = time.perf_counter()
    row = {
        "id": task.recording_id,
        "group": task.group,
        "speaker": task.speaker,
        "sex": task.sex,
        "age": task.age,
        "expected_syllables": task.transcript.syllables,
        "countable": task.transcript.countable,
        "repeats": task.transcript.repeats,
        "unclear": task.transcript.unclear,
        "tags": task.transcript.tags,
        "algorithm_version": ALGORITHM_VERSION,
        "error": None,
    }
    try:
        with zipfile.ZipFile(audio_zip) as archive, archive.open(task.member) as stream:
            row.update(analyze_recording(stream))
    except Exception as error:  # 녹음 하나의 실패가 나머지를 막지 않게 한다
        row["error"] = f"{type(error).__name__}: {error}"
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


def read_results(path: Path) -> list[dict]:
    """
    결과 파일(JSON Lines)을 읽음
    입력: path (결과 파일 경로. 없어도 됨)
    출력: 결과 사전 목록. 파일이 없으면 빈 목록. 같은 id가 여러 번 있으면 마지막 것만 남김
    알고리즘
    1. 줄마다 JSON으로 읽어 id를 키로 모음(뒤의 줄이 앞의 줄을 덮음)
    """
    if not path.exists():
        return []
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return list(rows.values())


def run(audio_zip: Path, labels_zip: Path, out_path: Path, group: str | None, limit: int | None, workers: int, out: Callable[[str], None]) -> int:
    """
    녹음을 계산해 결과 파일에 한 줄씩 더함. 이미 있는 녹음은 건너뜀
    입력: audio_zip (음성 zip), labels_zip (라벨 zip), out_path (결과 파일, JSON Lines), group (질환 폴더 이름의 앞 글자, 선택), limit (앞에서 이만큼만, 선택), workers (동시에 계산할 프로세스 수), out (진행 상황을 출력할 함수)
    출력: 실패한 녹음 수. 결과 파일에 녹음마다 한 줄을 더함(끝날 때마다 바로 씀)
    알고리즘
    1. 계산할 목록을 만들고(load_tasks), 결과 파일에 오류 없이 들어 있는 녹음은 뺌. limit이 있으면 앞에서 그만큼만 남김
    2. workers가 1이면 이 프로세스에서 차례로, 아니면 프로세스 풀로 계산함(compute)
    3. 녹음이 끝날 때마다 결과를 파일 끝에 쓰고, 몇 번째인지와 걸린 시간을 출력함
    4. 끝나면 계산한 녹음 길이 합, 걸린 시간, 실패 수를 출력함
    """
    tasks, audio_only, label_only = load_tasks(audio_zip, labels_zip, group)
    done = {row["id"] for row in read_results(out_path) if not row.get("error")}
    todo = [task for task in tasks if task.recording_id not in done]
    if limit is not None:
        todo = todo[:limit]
    already = sum(task.recording_id in done for task in tasks)
    out(f"짝이 맞는 녹음 {len(tasks)}개(음성만 {audio_only}개, 라벨만 {label_only}개). 이미 계산한 것 {already}개, 이번에 계산할 것 {len(todo)}개")
    if not todo:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    failed = 0
    audio_s = 0.0

    def record(index: int, row: dict) -> None:
        """
        결과 한 줄을 파일에 쓰고 진행 상황을 출력함
        입력: index (몇 번째로 끝났는지, 1부터), row (결과 사전)
        출력: 없음 (결과 파일 끝에 한 줄을 더하고, 실패 수와 녹음 길이 합을 고침)
        알고리즘
        1. JSON 한 줄로 파일 끝에 씀
        2. 오류가 있으면 실패로 세고, 없으면 녹음 길이를 더함
        """
        nonlocal failed, audio_s
        with out_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        if row.get("error"):
            failed += 1
            out(f"  {index}/{len(todo)} {row['id']}: 실패({row['error']})")
        else:
            audio_s += row["duration_s"]
            out(f"  {index}/{len(todo)} {row['id']}: {row['duration_s'] / 60:.1f}분 녹음, {row['seconds']:.0f}초")

    if workers <= 1:
        for index, task in enumerate(todo, 1):
            record(index, compute(str(audio_zip), task))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(compute, str(audio_zip), task) for task in todo]
            for index, future in enumerate(as_completed(futures), 1):
                record(index, future.result())
    elapsed = time.perf_counter() - started
    out(f"끝: 녹음 {audio_s / 3600:.1f}시간을 {elapsed / 60:.1f}분에 계산함. 실패 {failed}개")
    return failed


def median(values: list[float]) -> float | None:
    """
    중앙값을 구함
    입력: values (숫자 목록)
    출력: 중앙값. 목록이 비면 None
    알고리즘
    1. 비어 있으면 None, 아니면 numpy 중앙값을 실수로 돌려줌
    """
    return float(np.median(values)) if values else None


def mark_levels(rows: list[dict]) -> dict[str, str]:
    """
    화자마다 전사 표시(*, +) 비율로 세 무리 중 하나를 정함
    입력: rows (결과 사전 목록)
    출력: 화자 → "표시 없음" / "표시 적음" / "표시 많음"
    알고리즘
    1. 화자마다 그 화자 녹음 전체의 (* 수 + + 수) ÷ 전사 음절 수를 구함
    2. 비율이 0이면 "표시 없음"
    3. 나머지 화자는 비율의 중앙값 이하면 "표시 적음", 넘으면 "표시 많음". 의사가 매긴 중증도가 아니라 전사에 남은 표시로 나눈 것임
    """
    marks: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for row in rows:
        marks[row["speaker"]] += row["repeats"] + row["unclear"]
        total[row["speaker"]] += row["expected_syllables"]
    rates = {name: marks[name] / total[name] if total[name] else 0.0 for name in total}
    positive = [rate for rate in rates.values() if rate > 0]
    cut = float(np.median(positive)) if positive else 0.0
    return {name: MARK_LEVELS[0] if rate == 0 else MARK_LEVELS[1] if rate <= cut else MARK_LEVELS[2] for name, rate in rates.items()}


def summarize(rows: list[dict]) -> dict:
    """
    한 무리의 녹음 결과를 요약함
    입력: rows (오류 없이 계산된 결과 사전 목록)
    출력: 요약 사전(아래 키). 값이 없으면 None
    알고리즘
    1. recordings(녹음 수), speakers(화자 수), hours(녹음 길이 합, 시간), speech_hours(발화 시간 합, 시간)
    2. 음절 수: countable이고 전사 음절이 있고 발화가 있는 녹음만 씀. syllable_recordings(그 수), ratio_median(검출 ÷ 전사의 중앙값), error_median·error_q1·error_q3(오차율 |검출 − 전사| ÷ 전사의 중앙값과 25·75백분위), over_reading_error_pct(오차율이 READING_ERROR_RATIO 30% 이상인 녹음 비율, %)
    3. 조음속도: 같은 녹음에서 전사 음절 ÷ 발화 시간. rate_median(음절/초), implausible_rate_pct(PLAUSIBLE_RATE 1~10 음절/초 밖인 녹음 비율, %)
    4. 버린 조각: short_utterance_pct(발화가 MIN_UTTERANCE_SPEECH_S 0.5초보다 짧아 버린 조각 수 ÷ (쓴 조각 수 + 버린 조각 수), %)
    5. 기본주파수(발화가 있는 녹음): voiced_median(기본 범위 유성 비율의 중앙값), low_reliability_utterance_pct(신뢰 낮음 조각 수 합 ÷ 조각 수 합, %), low_voiced_recording_pct(녹음 전체 유성 비율이 0.5 미만인 녹음 비율, %), voiced_median_low_floor(아래쪽 50 Hz일 때 유성 비율의 중앙값)
    6. 화자별 낮은 목소리: 화자마다 50 Hz 범위 유성 값 중 75 Hz 아래 비율(프레임 합으로)을 구해, 5%를 넘는 화자 수 speakers_below_floor와 그중 남성 수 male_below_floor, 남성 화자 수 male_speakers
    """
    spoken = [row for row in rows if row["speech_frames"] > 0]
    counted = [row for row in spoken if row["countable"] and row["expected_syllables"] > 0]
    ratios = [row["detected_syllables"] / row["expected_syllables"] for row in counted]
    errors = [abs(ratio - 1) for ratio in ratios]
    rates = [row["expected_syllables"] / row["speech_s"] for row in counted if row["speech_s"] > 0]
    voiced = [row["voiced_frames"] / row["speech_frames"] for row in spoken]
    voiced_low = [row["voiced_frames_low_floor"] / row["speech_frames_low_floor"] for row in spoken if row["speech_frames_low_floor"] > 0]
    utterances = sum(row["utterances"] for row in spoken)
    skipped = sum(row["skipped_short"] for row in rows)

    below: dict[str, int] = defaultdict(int)
    frames_low: dict[str, int] = defaultdict(int)
    sex = {}
    for row in rows:
        sex[row["speaker"]] = row["sex"]
        below[row["speaker"]] += row["below_default_floor"]
        frames_low[row["speaker"]] += row["voiced_frames_low_floor"]
    low_speakers = [name for name in frames_low if frames_low[name] and below[name] / frames_low[name] > 0.05]

    def pct(part: int, whole: int) -> float | None:
        """
        비율을 퍼센트로 구함
        입력: part (부분), whole (전체)
        출력: 100 × part ÷ whole(%). whole이 0이면 None
        알고리즘
        1. whole이 0이면 None, 아니면 퍼센트를 돌려줌
        """
        return 100 * part / whole if whole else None

    return {
        "recordings": len(rows),
        "speakers": len({row["speaker"] for row in rows}),
        "hours": sum(row["duration_s"] for row in rows) / 3600,
        "speech_hours": sum(row["speech_s"] for row in rows) / 3600,
        "syllable_recordings": len(counted),
        "ratio_median": median(ratios),
        "error_median": median(errors),
        "error_q1": float(np.percentile(errors, 25)) if errors else None,
        "error_q3": float(np.percentile(errors, 75)) if errors else None,
        "over_reading_error_pct": pct(sum(error >= READING_ERROR_RATIO for error in errors), len(errors)),
        "rate_median": median(rates),
        "implausible_rate_pct": pct(sum(not PLAUSIBLE_RATE[0] <= rate <= PLAUSIBLE_RATE[1] for rate in rates), len(rates)),
        "short_utterance_pct": pct(skipped, sum(row["utterances"] for row in rows) + skipped),
        "voiced_median": median(voiced),
        "low_reliability_utterance_pct": pct(sum(row["low_reliability_utterances"] for row in spoken), utterances),
        "low_voiced_recording_pct": pct(sum(value < MIN_VOICED_RATIO for value in voiced), len(voiced)),
        "voiced_median_low_floor": median(voiced_low),
        "speakers_below_floor": len(low_speakers),
        "male_below_floor": sum(sex[name] == "M" for name in low_speakers),
        "male_speakers": sum(value == "M" for value in sex.values()),
    }


def build_report(rows: list[dict]) -> dict:
    """
    결과 전체를 질환별, 과제 종류별, 전사 표시 수준별로 요약함
    입력: rows (결과 파일에서 읽은 사전 목록)
    출력: 사전. algorithm_versions(결과에 든 알고리즘 버전), failed(실패한 녹음 수), all(전체 요약), by_group(질환 폴더 → 요약), by_task(과제 종류 → 요약), by_mark_level(표시 수준 → 요약), formats(WAV 형식별 녹음 수)
    알고리즘
    1. 오류가 있는 줄은 실패로 세고 뺌
    2. 전체, 질환 폴더별, 과제 종류(task_kind)별, 화자의 전사 표시 수준(mark_levels)별로 summarize를 구함
    """
    good = [row for row in rows if not row.get("error")]
    levels = mark_levels(good)
    by_group: dict[str, list[dict]] = defaultdict(list)
    by_level: dict[str, list[dict]] = defaultdict(list)
    by_task: dict[str, list[dict]] = defaultdict(list)
    for row in good:
        by_group[row["group"]].append(row)
        by_task[task_kind(row["id"])].append(row)
        by_level[levels[row["speaker"]]].append(row)
    formats = Counter(f"{row['sample_rate']} Hz, {row['channels']}채널, {row['bits']}비트" for row in good)
    return {
        "algorithm_versions": sorted({row["algorithm_version"] for row in good}),
        "failed": len(rows) - len(good),
        "all": summarize(good) if good else None,
        "by_group": {name: summarize(by_group[name]) for name in sorted(by_group)},
        "by_task": {name: summarize(by_task[name]) for name in (*TASK_KINDS.values(), OTHER_TASK) if name in by_task},
        "by_mark_level": {name: summarize(by_level[name]) for name in MARK_LEVELS if name in by_level},
        "formats": dict(formats.most_common()),
    }


def number(value: float | None, digits: int = 2, suffix: str = "") -> str:
    """
    숫자를 표에 넣을 글로 바꿈
    입력: value (숫자 또는 None), digits (소수 자리 수), suffix (뒤에 붙일 글, 예: %)
    출력: 문자열. None이면 "-"
    알고리즘
    1. None이면 "-", 아니면 자리 수에 맞춰 적고 suffix를 붙임
    """
    return "-" if value is None else f"{value:.{digits}f}{suffix}"


def render(report: dict) -> str:
    """
    요약을 마크다운 표로 만듦
    입력: report (build_report의 결과)
    출력: 여러 줄 문자열. 머리말, 질환별 표 두 개(음절·말속도, 기본주파수), 과제 종류별 표 두 개, 전사 표시 수준별 표 두 개, WAV 형식별 녹음 수
    알고리즘
    1. 결과가 없으면 "결과가 없습니다"만 돌려줌
    2. 머리말에 알고리즘 버전, 녹음 수·화자 수·분량, 실패 수를 적음
    3. 무리마다 한 줄씩, 음절·말속도 표와 기본주파수 표를 만듦. 질환 폴더 이름은 번호 뒤 이름만 적고, 맨 끝에 전체 줄을 둠
    4. 과제 종류별로 같은 표를 만들고, 앱의 읽기 문장과 같은 종류가 무엇인지와 낱말 읽기의 한계를 적음
    5. 전사 표시 수준별로 같은 표를 만들고, 표시로 나눈 것이 중증도가 아니라는 점을 적음
    """
    total = report["all"]
    if total is None:
        return "결과가 없습니다."

    def tables(groups: dict[str, dict], label: str) -> list[str]:
        """
        무리별 요약으로 표 두 개를 만듦
        입력: groups (무리 이름 → 요약), label (첫 칸 머리글)
        출력: 표의 줄 목록(음절·말속도 표, 빈 줄, 기본주파수 표)
        알고리즘
        1. 무리마다 음절·말속도 값과 기본주파수 값을 한 줄씩 적음
        """
        first = [
            f"| {label} | 녹음 | 화자 | 분량(시간) | 음절 비교 녹음 | 검출 ÷ 전사(중앙) | 음절 수 오차율(중앙, 25~75%) | 오차율 30% 이상 | 조음속도(중앙, 음절/초) | 조음속도 1~10 밖 | 짧아서 버린 조각 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        second = [
            f"| {label} | 유성 비율(중앙) | 신뢰 낮음 조각 | 유성 비율 0.5 미만 녹음 | 50 Hz로 내린 유성 비율(중앙) | 75 Hz 아래가 5% 넘는 화자 | 그중 남성 / 남성 화자 |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, item in groups.items():
            spread = f"{number(item['error_median'])} ({number(item['error_q1'])}~{number(item['error_q3'])})"
            first.append(
                f"| {name} | {item['recordings']} | {item['speakers']} | {item['hours']:.1f} | {item['syllable_recordings']} | "
                f"{number(item['ratio_median'])} | {spread} | {number(item['over_reading_error_pct'], 0, '%')} | "
                f"{number(item['rate_median'])} | {number(item['implausible_rate_pct'], 0, '%')} | {number(item['short_utterance_pct'], 0, '%')} |"
            )
            second.append(
                f"| {name} | {number(item['voiced_median'])} | {number(item['low_reliability_utterance_pct'], 0, '%')} | "
                f"{number(item['low_voiced_recording_pct'], 0, '%')} | {number(item['voiced_median_low_floor'])} | "
                f"{item['speakers_below_floor']} | {item['male_below_floor']} / {item['male_speakers']} |"
            )
        return [*first, "", *second]

    named = {name.split(".", 1)[-1]: item for name, item in report["by_group"].items()}
    lines = [
        f"AI Hub 구음장애 음성으로 본 서버 지표 계산(알고리즘 버전 {', '.join(report['algorithm_versions'])})",
        f"- 녹음 {total['recordings']}개, 화자 {total['speakers']}명, {total['hours']:.1f}시간(발화 {total['speech_hours']:.1f}시간). 계산 실패 {report['failed']}개",
        "- 음절 수 오차율 = |검출 음절 수 − 전사 음절 수| ÷ 전사 음절 수(녹음 하나 단위). 숫자·영문이 든 전사는 뺌",
        "- 유성 비율 = 발화 프레임 중 기본주파수를 잡은 프레임의 비율(탐색 범위 75~500 Hz). 0.5 미만이면 서버가 신뢰 낮음으로 표시함",
        "- 짧아서 버린 조각 = 발화가 0.5초보다 짧아 계산에서 뺀 조각의 비율",
        "",
        "질환별",
        *tables({**named, "전체": total}, "질환"),
        "",
        "과제 종류별. 앱의 읽기 문장과 같은 종류는 문장 읽기와 문단 읽기임. 낱말 읽기는 낱말 하나가 짧은 조각이 되어 많이 버려지므로 검출 음절 수가 적게 나옴",
        *tables(report["by_task"], "과제"),
        "",
        "전사 표시(*, +) 수준별. 의사가 매긴 중증도가 아니라 전사에 남은 표시로 화자를 나눈 것이고, 표시가 드물어 차이가 작음",
        *tables(report["by_mark_level"], "표시 수준"),
        "",
        "WAV 형식별 녹음 수: " + ", ".join(f"{name} {count}개" for name, count in report["formats"].items()),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None, out: Callable[[str], None] = print) -> int:
    """
    AI Hub 음성 분석 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). "run --audio 음성zip --labels 라벨zip --out 결과파일 [--group 글자] [--limit 수] [--workers 수]" 또는 "report 결과파일 [--json]"
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 끝남, 1 = run에서 실패한 녹음이 있음, 2 = 파일이 없음). 진행 상황과 표를 out으로, 입력 오류를 표준 오류로 씀. run은 결과 파일에 줄을 더함
    알고리즘
    1. 명령줄 인자를 읽음
    2. run이면 두 zip이 있는지 확인하고(없으면 2) run을 실행해, 실패가 있으면 1을 돌려줌
    3. report면 결과 파일을 읽어(없으면 2) build_report로 요약하고, --json이면 JSON으로, 아니면 render로 출력함
    """
    parser = argparse.ArgumentParser(description="AI Hub 구음장애 음성으로 서버 지표 계산을 점검한다")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="녹음을 계산해 결과 파일에 더한다")
    run_parser.add_argument("--audio", type=Path, required=True, help="음성 zip(원천데이터)")
    run_parser.add_argument("--labels", type=Path, required=True, help="라벨 zip(라벨링데이터)")
    run_parser.add_argument("--out", type=Path, required=True, help="결과 파일(JSON Lines)")
    run_parser.add_argument("--group", help="질환 폴더 이름이 이 글자로 시작하는 것만(예: 11)")
    run_parser.add_argument("--limit", type=int, help="앞에서 이만큼만 계산한다")
    run_parser.add_argument("--workers", type=int, default=1, help="동시에 계산할 프로세스 수(기본 1)")
    report_parser = commands.add_parser("report", help="결과 파일을 요약한다")
    report_parser.add_argument("results", type=Path, help="결과 파일(JSON Lines)")
    report_parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    args = parser.parse_args(argv)

    if args.command == "run":
        missing = [str(path) for path in (args.audio, args.labels) if not path.is_file()]
        if missing:
            print(f"파일이 없습니다: {', '.join(missing)}", file=sys.stderr)
            return 2
        return 1 if run(args.audio, args.labels, args.out, args.group, args.limit, args.workers, out) else 0

    if not args.results.is_file():
        print(f"파일이 없습니다: {args.results}", file=sys.stderr)
        return 2
    report = build_report(read_results(args.results))
    out(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
