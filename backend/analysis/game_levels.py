"""게임 단계표의 기준이 환자 음성에서 도달 가능한 값인지 본다(AI Hub 구음장애 음성의 문장·문단 읽기).

게임 판정은 앱(TypeScript)이 한다. 그래서 앱의 판정 코드를 고치지 않고 node로 그대로 불러(game_levels_bridge.mjs),
환자 음성의 발화 조각마다 게임이 내는 것과 같은 값을 구한다. 발화 조각을 찾는 것만 서버 코드로 한다(analysis.aihub와 같음).
보는 것
- 노래방 따라 읽기(guide_reading)의 변동계수 기준: 환자의 발화 조각 가운데 몇 %가 단계별 기준을 넘는가
- 기기 방식과 서버 방식의 차이: 발화 시간, 음절 핵 수, 기본주파수를 잡는 비율
- 조각 수가 전사의 문장 수와 같은 녹음: 조각마다 실제 음절 수를 알 수 있으므로 말속도 허용 폭을 따로 본다
- 우주선(spaceship)의 실시간 기본주파수 추적기가 환자 음성에서 얼마나 끊기는가(참고)
환자 개인을 평가하지 않고, 낭독 녹음이므로 "환자가 낼 수 있는 범위"까지만 본다. 안내를 따라 읽는 능력은 알 수 없다.
backend 폴더에서 실행한다. Node.js 24와 frontend의 node_modules(npm ci)가 필요하다. DB·S3는 쓰지 않는다.
    python -m analysis.game_levels run --audio 음성.zip --labels 라벨.zip --out 결과.jsonl
        --group 11      폴더 이름이 이 글자로 시작하는 질환만(예: 11 = 중풍)
        --limit 5       앞에서 5개만
        --workers 4     동시에 계산할 프로세스 수(기본 1)
        --frontend 경로  frontend 폴더(기본은 저장소의 frontend)
        이미 결과 파일에 있는 녹음은 건너뛴다.
    python -m analysis.game_levels report 결과.jsonl [--json]

계산 방법
- 녹음을 5분씩 읽는다. 발화 조각이 덩어리 경계에서 잘리지 않게, 끝에 걸친 조각은 다음 덩어리와 함께 계산한다.
- 발화 조각은 서버 규칙으로 찾는다(1초 넘게 쉬면 다른 조각, 발화 0.5초 미만 조각은 버림). 서버 쪽 잡음 바닥은 그 덩어리 프레임 음량의 하위 10백분위다.
- 조각마다 앞뒤 0.5초를 붙여 원래 샘플레이트 그대로 앱 코드에 넣는다.
- 앱 코드는 잡음 바닥 + 10 dB를 넘는 소리를 발화로 본다. 앱에서 측정 블록 잡음 바닥이 들어가는 자리에는 말소리 주변 소음(그 덩어리에서
  조각 앞뒤 0.5초 가운데 발화가 아닌 프레임 음량의 중앙값)을 넣는다. 이 자료는 발화 사이가 거의 무음으로 지워진 녹음이 많아,
  하위 10백분위를 쓰면 방 소음까지 발화로 잡히기 때문이다.
- 말속도 = N(음절 수) ÷ T_speech(앱 코드가 구한 발화 시간, 초). 게임과 같은 정의다. N(음절 수)은 조각 수와 전사 문장 수가 같은 녹음에서만 안다.
- 기준값(조각 길이, 짝 맞춤 조건, "충분하다"고 보는 화자 수)은 모두 초기값이다.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import BinaryIO

import numpy as np

from analysis import aihub
from analysis.aihub import (
    BLOCK_S,
    CLIP_MARGIN_S,
    DEFAULT_CEILING_HZ,
    DEFAULT_FLOOR_HZ,
    DUAL,
    GROUP_NAMES,
    MIN_UTTERANCE_SPEECH_S,
    UTTERANCE_GAP_S,
    Task,
    clean_transcript,
    number,
    read_native_blocks,
    read_results,
    task_kind,
    utterance_groups,
)
from app.metrics import pitch, syllables
from app.metrics.audio import FRAME, HOP, HOP_S, RATE, frames, to_16k
from app.metrics.pitch import TIME_STEP_S
from app.metrics.session import frame_at
from app.metrics.vad import get_detector
from app.metrics.version import ALGORITHM_VERSION

BRIDGE = Path(__file__).with_name("game_levels_bridge.mjs")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
BRIDGE_TIMEOUT_S = 600

SENTENCE_TASKS = ("문장 읽기", "자기소개 문장", "문단 읽기")  # 앱의 문장 읽기와 같은 종류(aihub.TASK_KINDS의 02·03·04)
DEVICE_MARGIN_S = 0.5  # 앱 코드에 넣는 조각 앞뒤에 붙이는 조용한 부분(게임 녹음에는 앞 1초 준비와 끝의 무음이 있다)
MAX_CARRY_S = 60.0  # 덩어리 끝에 걸친 조각을 다음 덩어리로 넘길 때의 최대 길이. 넘으면 덩어리 경계에서 자른다
SENTENCE_END = re.compile(r"[.?!]+|\s{2,}")  # 전사에서 문장이 끝나는 곳(마침표·물음표·느낌표, 또는 두 칸 이상 띄움)
GROSS_ST = 3.0  # 기기 기본주파수가 서버 값과 이만큼(반음) 넘게 다르면 크게 다른 것으로 센다
MATCH_MIN_SENTENCES = 5  # 조각과 문장을 짝짓는 녹음의 최소 문장 수
MATCH_MIN_CORR = 0.7  # 조각 발화 시간과 문장 음절 수의 상관이 이보다 낮으면 짝이 어긋난 것으로 본다
GAME_SYLLABLES = (6, 20)  # 게임 문장(11~16음절)과 비슷한 길이로 보는 음절 수 범위
MIN_PIECE_SPEECH_S = 1.0  # 이보다 짧은 조각은 게임 문장과 비교하지 않는다
ENOUGH_PIECES = 10  # 화자별 값을 낼 때 필요한 조각 수
ENOUGH_SPEAKERS = 30  # 실제 음절 수를 아는 화자가 이보다 적으면 말속도 허용 폭은 "정할 수 없다"고 본다
SPACESHIP_MIN_COVERAGE = 0.5  # 우주선 게임이 판정을 보류하는 기준(spaceship/metrics.ts의 MIN_COVERAGE와 같은 값)
DETECTION_BINS = ((0.0, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, float("inf")))  # 기기가 찾은 음절 핵 ÷ 음절 수의 구간

Device = Callable[[list[np.ndarray], int, float], dict]


def sentence_syllables(text: str) -> list[int] | None:
    """
    전사를 문장으로 나눠 문장마다 음절 수를 셈
    입력: text (전사 원문. 녹음 하나 전체)
    출력: 문장별 한글 음절 수 목록(음절이 없는 토막은 뺌). 숫자나 영문이 있어 셀 수 없는 문장이 있으면 None
    알고리즘
    1. 이중 전사 "(철자)/(발음)"은 발음만 남김(괄호 안의 문장부호에서 나뉘지 않게 먼저 함)
    2. 마침표·물음표·느낌표, 또는 두 칸 이상 띄운 곳에서 나눔
    3. 토막마다 clean_transcript로 음절 수를 세고, 0인 토막은 버림
    """
    spoken = DUAL.sub(lambda match: match.group(2), text)
    counts = []
    for part in SENTENCE_END.split(spoken):
        cleaned = clean_transcript(part)
        if not cleaned.countable:
            return None
        if cleaned.syllables:
            counts.append(cleaned.syllables)
    return counts


def run_bridge(frontend: Path, clips: list[np.ndarray], rate: int, floor: float) -> dict:
    """
    발화 조각들을 앱의 판정 코드(node)로 계산함
    입력: frontend (frontend 폴더), clips (조각별 모노 표본 배열, 원래 샘플레이트), rate (샘플레이트, Hz), floor (잡음 바닥, dBFS)
    출력: game_levels_bridge.mjs가 낸 사전. guide_levels(노래방 따라 읽기 단계표), up_at_pct·down_at_pct, clips(조각별 speech_s, cv, nuclei, lead_db, t0_s, hop_s, hz). node가 없거나 실패하면 RuntimeError
    알고리즘
    1. 조각들을 이어 붙여 임시 파일(32비트 실수)에 쓰고, 조각마다 시작 위치와 길이를 적은 JSON을 node의 표준 입력으로 줌
    2. node가 표준 출력으로 낸 JSON을 읽음. 종료 코드가 0이 아니면 표준 오류의 끝부분을 담아 RuntimeError를 냄
    """
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node를 찾지 못했습니다. Node.js 24를 설치하세요")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clips.f32"
        offsets = np.cumsum([0, *[len(clip) for clip in clips]])
        if clips:
            np.concatenate(clips).astype("<f4").tofile(path)
        request = {
            "frontend": str(frontend),
            "file": str(path),
            "rate": rate,
            "min_hz": DEFAULT_FLOOR_HZ,
            "max_hz": DEFAULT_CEILING_HZ,
            "lead_s": DEVICE_MARGIN_S,
            "floor": floor,
            "clips": [{"offset": int(offset), "length": len(clip)} for offset, clip in zip(offsets, clips)],
        }
        done = subprocess.run(
            [node, "--no-warnings", str(BRIDGE)], input=json.dumps(request), capture_output=True, text=True, timeout=BRIDGE_TIMEOUT_S, check=False
        )
    if done.returncode != 0:
        raise RuntimeError(f"앱 판정 코드 실행에 실패했습니다(종료 코드 {done.returncode}): {done.stderr.strip()[-500:]}")
    return json.loads(done.stdout)


def cut_buffer(groups: list[list[tuple[int, int]]], total_frames: int, last: bool) -> tuple[list[list[tuple[int, int]]], int | None]:
    """
    덩어리에서 지금 계산할 조각과 다음 덩어리로 넘길 위치를 정함. 덩어리 끝에 걸친 조각이 둘로 잘리지 않게 하기 위함
    입력: groups (조각 목록. 조각 하나는 발화 구간 (시작 프레임, 끝 프레임) 목록), total_frames (덩어리의 프레임 수), last (녹음의 마지막 덩어리인지)
    출력: (지금 계산할 조각 목록, 다음 덩어리 앞에 붙일 부분의 시작 프레임). 넘길 것이 없으면 시작 프레임은 None
    알고리즘
    1. 마지막 덩어리면 모든 조각을 계산하고 넘기지 않음
    2. 조각의 끝이 덩어리 끝에서 1.5초(조각을 나누는 1초 + 앞뒤 여유 0.5초) 안쪽이면 그 조각과 그 뒤는 미룸. 다음 덩어리에서 말이 이어질 수 있어서임
    3. 넘길 부분은 미룬 첫 조각의 0.5초 앞부터, 미룬 조각이 없으면 덩어리 끝 1.5초부터임
    4. 넘길 부분이 MAX_CARRY_S(60초)보다 길면(쉬지 않고 이어진 말) 미루지 않고 모두 계산함. 그 경우 마지막 조각은 경계에서 잘릴 수 있음
    """
    if last:
        return groups, None
    tail = frames(UTTERANCE_GAP_S) + frames(DEVICE_MARGIN_S)
    ready = [group for group in groups if group[-1][1] <= total_frames - tail]
    held = groups[len(ready) :]
    start = max(held[0][0][0] - frames(DEVICE_MARGIN_S), 0) if held else max(total_frames - tail, 0)
    if (total_frames - start) * HOP_S > MAX_CARRY_S:
        return groups, None
    return ready, start


def compare_pitch(server_times: np.ndarray, server_hz: np.ndarray, device_times: np.ndarray, device_hz: np.ndarray, in_speech: np.ndarray) -> dict:
    """
    같은 조각에서 기기 추적기와 서버(Praat)가 낸 기본주파수를 프레임마다 견줌
    입력: server_times (서버 기본주파수 프레임 시각, 초), server_hz (서버 값, Hz. 무성은 nan), device_times (기기 프레임 시각, 초. 같은 기준), device_hz (기기 값, Hz. 못 잡으면 nan), in_speech (기기 프레임마다 서버 발화 구간 안인지)
    출력: 사전. f0_ref(발화 구간 안에서 서버가 유성으로 본 기기 프레임 수), f0_both(그중 기기도 값을 낸 수), f0_gross(둘 다 값을 냈는데 GROSS_ST 3반음 넘게 다른 수), f0_dev_only(서버는 무성인데 기기만 값을 낸 수)
    알고리즘
    1. 기기 프레임마다 시각이 가장 가까운 서버 프레임을 찾음. 0.01초(서버 프레임 간격)보다 멀면 짝이 없는 것으로 봄
    2. 발화 구간 안의 프레임만 셈
    3. 차이(반음) = 12 × log₂(F_device(기기 기본주파수, Hz) ÷ F_server(서버 기본주파수, Hz))
    """
    if len(server_times) == 0 or len(device_times) == 0:
        return {"f0_ref": 0, "f0_both": 0, "f0_gross": 0, "f0_dev_only": 0}
    index = np.clip(np.round((device_times - server_times[0]) / TIME_STEP_S).astype(int), 0, len(server_times) - 1)
    near = np.abs(server_times[index] - device_times) <= TIME_STEP_S
    server = np.where(near, server_hz[index], np.nan)
    ref = in_speech & ~np.isnan(server)
    both = ref & ~np.isnan(device_hz)
    difference = np.abs(12 * np.log2(device_hz[both] / server[both]))
    return {
        "f0_ref": int(ref.sum()),
        "f0_both": int(both.sum()),
        "f0_gross": int((difference > GROSS_ST).sum()),
        "f0_dev_only": int((in_speech & np.isnan(server) & ~np.isnan(device_hz)).sum()),
    }


def measure_buffer(buffer: np.ndarray, rate: int, start_s: float, device: Device, detector, last: bool) -> tuple[list[dict], int, int | None, dict]:
    """
    원래 샘플레이트 표본 한 덩어리에서 발화 조각을 찾아 조각마다 서버 방식과 기기 방식으로 계산함
    입력: buffer (모노 표본, 원래 샘플레이트), rate (샘플레이트, Hz), start_s (이 덩어리가 녹음에서 시작하는 시각, 초), device (조각 목록·샘플레이트·잡음 바닥을 받아 앱 판정 코드의 결과 사전을 돌려주는 함수), detector (발화 검출기), last (마지막 덩어리인지)
    출력: (조각별 결과 목록, 짧아서 버린 조각 수, 다음 덩어리 앞에 붙일 부분의 시작 표본 위치 또는 None, 앱 단계표 사전(guide_levels, up_at_pct, down_at_pct. 앱 코드를 부르지 않았으면 빈 사전))
        조각 결과: t(녹음 안 시작 시각, 초), speech_s·pause_s(서버 발화 시간·휴지 시간, 초), nuclei(서버 검출 음절 수, 실패하면 None), floor_db(서버 쪽 잡음 바닥, dBFS), ambient_db(앱 코드에 넣은 잡음 바닥 = 말소리 주변 소음, dBFS), lead_db(조각 앞 0.5초의 서버 프레임 음량 중앙값, dBFS. 프레임이 없으면 None),
        d_speech_s(기기 발화 시간, 초), d_cv(기기 변동계수, 못 구하면 None), d_nuclei(기기 음절 핵 수), d_lead_db(조각 앞 0.5초의 기기 음량 중앙값, dBFS), compare_pitch의 네 값
    알고리즘
    1. 16 kHz로 바꿔 발화 조각을 찾고(utterance_groups), 지금 계산할 조각을 고름(cut_buffer)
    2. 발화가 MIN_UTTERANCE_SPEECH_S(0.5초)보다 짧은 조각은 버리고 수만 셈
    3. 조각마다 서버 방식: 앞뒤 0.3초를 붙인 16 kHz 조각에서 음절 핵 수(count_nuclei)와 기본주파수(75~500 Hz, 보정)를 구함
    4. 말소리 주변 소음 = 계산할 조각들의 앞뒤 0.5초 가운데 발화가 아닌 프레임 음량의 중앙값. 그런 프레임이 없으면 서버 쪽 잡음 바닥을 씀
    5. 조각마다 기기 방식: 앞뒤 0.5초를 붙인 원래 샘플레이트 조각을 모아, 말소리 주변 소음을 잡음 바닥으로 주고 device로 한 번에 계산함
    6. 기기 추적기의 기본주파수를 서버 값과 견줌(compare_pitch). 시각은 모두 덩어리 시작 기준으로 맞춤
    """
    samples = to_16k(buffer, rate).astype(np.float32, copy=False)
    audio, floor, groups = utterance_groups(samples, detector)
    total = len(audio.levels)
    ready, keep_frame = cut_buffer(groups, total, last)
    keep_from = None if keep_frame is None else min(len(buffer), round(keep_frame * HOP / RATE * rate))

    mask = np.zeros(total, dtype=bool)
    for group in groups:
        for start, end in group:
            mask[start:end] = True
    around = np.zeros(total, dtype=bool)  # 계산할 조각의 앞뒤 0.5초
    skipped = 0
    pieces: list[dict] = []
    clips: list[np.ndarray] = []
    tracks = []
    for group in ready:
        speech_frames = sum(end - start for start, end in group)
        if speech_frames * HOP_S < MIN_UTTERANCE_SPEECH_S:
            skipped += 1
            continue
        first = max(group[0][0] - frames(CLIP_MARGIN_S), 0)
        final = min(group[-1][1] + frames(CLIP_MARGIN_S), total)
        clip = audio.samples[first * HOP : (final - 1) * HOP + FRAME]
        try:
            nuclei = syllables.count_nuclei(clip)
        except Exception:  # Praat이 아주 짧거나 조용한 조각에서 실패할 수 있다
            nuclei = None
        track = pitch.extract(clip, DEFAULT_FLOOR_HZ, DEFAULT_CEILING_HZ)
        tracks.append((first * HOP_S + track.times, pitch.clean(track.hz) if len(track.times) else track.hz))

        device_first = max(group[0][0] - frames(DEVICE_MARGIN_S), 0)
        device_final = min(group[-1][1] + frames(DEVICE_MARGIN_S), total)
        around[device_first : group[0][0]] = True
        around[group[-1][1] : device_final] = True
        lead = audio.levels[device_first : group[0][0]]
        begin = round(device_first * HOP / RATE * rate)
        clips.append(buffer[begin : min(len(buffer), round(((device_final - 1) * HOP + FRAME) / RATE * rate))])
        pieces.append(
            {
                "t": round(start_s + group[0][0] * HOP_S, 2),
                "speech_s": round(speech_frames * HOP_S, 2),
                "pause_s": round(sum(b[0] - a[1] for a, b in zip(group, group[1:])) * HOP_S, 2),
                "nuclei": nuclei,
                "floor_db": round(floor, 1),
                "lead_db": round(float(np.median(lead)), 2) if len(lead) else None,
                "begin_s": begin / rate,
            }
        )

    table = {}
    if clips:
        quiet = audio.levels[around & ~mask]
        ambient = float(np.median(quiet)) if len(quiet) else floor
        answer = device(clips, rate, ambient)
        table = {key: answer[key] for key in ("guide_levels", "up_at_pct", "down_at_pct")}
        for piece, measured, (server_times, server_hz) in zip(pieces, answer["clips"], tracks, strict=True):
            device_hz = np.array([np.nan if value is None else value for value in measured["hz"]], dtype=float)
            device_times = piece.pop("begin_s") + measured["t0_s"] + measured["hop_s"] * np.arange(len(device_hz))
            in_speech = mask[frame_at(device_times, total)] if len(device_times) else np.zeros(0, dtype=bool)
            piece.update(
                {
                    "ambient_db": round(ambient, 1),
                    "d_speech_s": measured["speech_s"],
                    "d_cv": measured["cv"],
                    "d_nuclei": measured["nuclei"],
                    "d_lead_db": measured["lead_db"],
                    **compare_pitch(server_times, server_hz, device_times, device_hz, in_speech),
                }
            )
    return pieces, skipped, keep_from, table


def analyze_recording(stream: BinaryIO, device: Device, detector=None, block_s: float = BLOCK_S) -> dict:
    """
    녹음 하나를 처음부터 끝까지 읽어 발화 조각마다 값을 구함
    입력: stream (WAV 바이트를 앞에서부터 읽을 수 있는 것), device (앱 판정 코드를 부르는 함수), detector (발화 검출기. 없으면 공용 검출기), block_s (한 번에 읽는 길이, 초)
    출력: 사전. duration_s(녹음 길이, 초), sample_rate(Hz), skipped_short(짧아서 버린 조각 수), pieces(조각별 결과 목록, measure_buffer 참고), guide_levels·up_at_pct·down_at_pct(앱의 단계표)
    알고리즘
    1. read_native_blocks로 block_s초씩 원래 샘플레이트 표본을 받음
    2. 앞 덩어리에서 넘긴 부분을 앞에 붙여 계산하고(measure_buffer), 끝에 걸친 조각은 다시 다음 덩어리로 넘김
    3. 조각이 하나도 없어 앱 코드를 부르지 않았으면, 단계표만 받으려고 빈 목록으로 한 번 부름
    """
    detector = detector or get_detector()
    info, blocks = read_native_blocks(stream, block_s)
    rate = info.sample_rate
    carry = np.empty(0, dtype=np.float32)
    offset = 0  # 지금 덩어리(넘긴 부분 포함)가 녹음에서 시작하는 표본 위치
    length = 0
    pieces: list[dict] = []
    skipped = 0
    table: dict = {}
    block = next(blocks, None)
    while block is not None:
        following = next(blocks, None)
        length += len(block)
        buffer = np.concatenate([carry, block]) if len(carry) else block
        found, short, keep_from, seen = measure_buffer(buffer, rate, offset / rate, device, detector, last=following is None)
        pieces += found
        skipped += short
        table = seen or table
        carry = buffer[keep_from:] if keep_from is not None else np.empty(0, dtype=np.float32)
        offset += keep_from if keep_from is not None else len(buffer)
        block = following
    if not table:
        answer = device([], rate, 0.0)
        table = {key: answer[key] for key in ("guide_levels", "up_at_pct", "down_at_pct")}
    return {"duration_s": round(length / rate, 2), "sample_rate": rate, "skipped_short": skipped, "pieces": pieces, **table}


def keep_task(task: Task) -> bool:
    """
    문장·문단 읽기 녹음인지 봄
    입력: task (녹음)
    출력: 과제 종류가 SENTENCE_TASKS(문장 읽기, 자기소개 문장, 문단 읽기)에 들면 참
    알고리즘
    1. 녹음 이름의 과제 번호로 과제 종류를 알아내(task_kind) 견줌
    """
    return task_kind(task.recording_id) in SENTENCE_TASKS


def compute(audio_zip: str, task: Task, frontend: str = str(FRONTEND)) -> dict:
    """
    녹음 하나를 계산해 결과 한 줄을 만듦(작업 프로세스에서 실행)
    입력: audio_zip (음성 zip 경로), task (계산할 녹음), frontend (frontend 폴더 경로)
    출력: 결과 사전. id, group, speaker, sex, age, task(과제 종류), sentences(전사의 문장별 음절 수, 셀 수 없으면 None), analyze_recording의 값, seconds(계산 시간, 초), algorithm_version, error(실패하면 "예외 이름: 내용", 아니면 None)
    알고리즘
    1. 전사를 문장으로 나눠 음절 수를 셈(sentence_syllables)
    2. 음성 zip에서 그 녹음을 열어 analyze_recording으로 계산함. 앱 판정 코드는 run_bridge로 부름
    3. 예외가 나면 error에 적고 나머지 값 없이 돌려줌(다른 녹음은 계속 계산함)
    """
    started = time.perf_counter()
    row = {
        "id": task.recording_id,
        "group": task.group,
        "speaker": task.speaker,
        "sex": task.sex,
        "age": task.age,
        "task": task_kind(task.recording_id),
        "sentences": sentence_syllables(task.text),
        "algorithm_version": ALGORITHM_VERSION,
        "error": None,
    }
    try:
        with zipfile.ZipFile(audio_zip) as archive, archive.open(task.member) as stream:
            row.update(analyze_recording(stream, partial(run_bridge, Path(frontend))))
    except Exception as error:  # 녹음 하나의 실패가 나머지를 막지 않게 한다
        row["error"] = f"{type(error).__name__}: {error}"
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


def matched_syllables(row: dict) -> list[int] | None:
    """
    조각과 전사 문장을 순서대로 짝지을 수 있는 녹음이면 조각별 실제 음절 수를 돌려줌
    입력: row (녹음 하나의 결과)
    출력: 조각 순서대로 놓인 음절 수 목록. 짝지을 수 없으면 None
    알고리즘
    1. 전사 문장을 셀 수 있고, 문장 수가 MATCH_MIN_SENTENCES(5) 이상이며, 조각 수와 문장 수가 같아야 함
    2. 조각의 서버 발화 시간과 문장 음절 수의 상관계수가 MATCH_MIN_CORR(0.7) 이상이어야 함. 수는 같아도 조각이 문장과 어긋난 녹음을 거르기 위함
    """
    counts = row.get("sentences")
    pieces = row.get("pieces") or []
    if not counts or len(counts) < MATCH_MIN_SENTENCES or len(counts) != len(pieces):
        return None
    durations = [piece["speech_s"] for piece in pieces]
    if np.std(durations) == 0 or np.std(counts) == 0:
        return None
    return counts if float(np.corrcoef(durations, counts)[0, 1]) >= MATCH_MIN_CORR else None


def flatten(rows: list[dict]) -> list[dict]:
    """
    녹음별 결과를 조각 하나가 한 줄인 목록으로 펼침
    입력: rows (오류 없이 계산된 녹음 결과 목록)
    출력: 조각 사전 목록. 조각 결과에 id, group, speaker를 더하고, true(실제 음절 수, 모르면 None), count(음절 수: true가 있으면 그것, 없으면 서버 검출 음절 수), usable(게임 문장과 비슷한 길이인지)을 둠
    알고리즘
    1. 녹음마다 조각별 실제 음절 수를 구함(matched_syllables)
    2. usable = 서버 발화 시간이 MIN_PIECE_SPEECH_S(1초) 이상이고 count가 GAME_SYLLABLES(6~20) 안. 기기 발화 시간이 0이면(앱 코드가 발화를 못 찾음) 거짓
    """
    out = []
    for row in rows:
        true = matched_syllables(row)
        for index, piece in enumerate(row["pieces"]):
            count = true[index] if true else piece["nuclei"]
            usable = (
                piece["speech_s"] >= MIN_PIECE_SPEECH_S
                and count is not None
                and GAME_SYLLABLES[0] <= count <= GAME_SYLLABLES[1]
                and piece["d_speech_s"] > 0
            )
            out.append({**piece, "id": row["id"], "group": row["group"], "speaker": row["speaker"], "true": true[index] if true else None, "count": count, "usable": usable})
    return out


def quartiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    """
    중앙값과 25·75백분위를 구함
    입력: values (숫자 목록)
    출력: (중앙값, 25백분위, 75백분위). 목록이 비면 모두 None
    알고리즘
    1. 비어 있으면 None 셋, 아니면 numpy 백분위(50, 25, 75)를 실수로 돌려줌
    """
    if not values:
        return None, None, None
    middle, low, high = np.percentile(values, [50, 25, 75])
    return float(middle), float(low), float(high)


def percent(part: float, whole: float) -> float | None:
    """
    비율을 퍼센트로 구함
    입력: part (부분), whole (전체)
    출력: 100 × part ÷ whole(%). whole이 0이면 None
    알고리즘
    1. whole이 0이면 None, 아니면 퍼센트를 돌려줌
    """
    return 100 * part / whole if whole else None


def speaker_shares(pieces: list[dict], passed: Callable[[dict], bool], up_at: float, down_at: float) -> dict:
    """
    화자마다 통과한 조각의 비율을 구해 요약함
    입력: pieces (조각 목록), passed (조각이 통과인지 정하는 함수), up_at (게임이 단계를 올리는 성공률, %), down_at (단계를 내리는 성공률, %)
    출력: 사전. speakers(조각이 ENOUGH_PIECES 10개 이상인 화자 수), share_median·share_q1·share_q3(화자별 통과 비율의 중앙값과 25·75백분위, %), up_pct(통과 비율이 up_at 이상인 화자의 비율, %), down_pct(통과 비율이 down_at 이하인 화자의 비율, %)
    알고리즘
    1. 화자별로 조각을 모아, 조각이 10개 이상인 화자만 통과 비율(%)을 구함
    2. 화자별 비율의 중앙값·25·75백분위와, up_at 이상·down_at 이하인 화자의 비율을 구함
    """
    by: dict[str, list[bool]] = defaultdict(list)
    for piece in pieces:
        by[piece["speaker"]].append(passed(piece))
    shares = [100 * sum(flags) / len(flags) for flags in by.values() if len(flags) >= ENOUGH_PIECES]
    middle, low, high = quartiles(shares)
    return {
        "speakers": len(shares),
        "share_median": middle,
        "share_q1": low,
        "share_q3": high,
        "up_pct": percent(sum(share >= up_at for share in shares), len(shares)),
        "down_pct": percent(sum(share <= down_at for share in shares), len(shares)),
    }


def cv_summary(pieces: list[dict], levels: list[list[float]], up_at: float, down_at: float) -> dict:
    """
    한 무리의 조각에서 변동계수 기준을 얼마나 넘는지 요약함
    입력: pieces (게임 문장과 비슷한 길이의 조각 목록), levels (단계표 [[단계, 허용 폭 %, 변동계수 기준], ...]), up_at·down_at (단계를 올리고 내리는 성공률, %)
    출력: 사전. pieces(조각 수), speakers(화자 수), no_cv_pct(변동계수를 구하지 못한 조각의 비율, %), cv_median·cv_q1·cv_q3(구한 변동계수의 중앙값과 25·75백분위), levels(단계별 사전 목록: level, cv_max, pass_pct(게임 규칙으로 통과한 조각 비율, %), measured_pass_pct(변동계수를 구한 조각만 볼 때 기준 이하인 비율, %), speaker_shares의 값)
    알고리즘
    1. 게임 규칙: 변동계수를 구하지 못한 조각은 박자를 보지 않고 통과로 둠(scoring.ts의 judgeSentence와 같음)
    2. 단계마다 변동계수 ≤ 기준인 조각의 비율과 화자별 요약을 구함
    """
    measured = [piece["d_cv"] for piece in pieces if piece["d_cv"] is not None]
    middle, low, high = quartiles(measured)
    out = []
    for level, _, cv_max in levels:

        def passed(piece: dict, limit: float = cv_max) -> bool:
            """
            조각이 이 단계의 변동계수 기준을 게임 규칙으로 통과하는지 봄
            입력: piece (조각), limit (변동계수 기준)
            출력: 변동계수를 구하지 못했거나 기준 이하이면 참
            알고리즘
            1. d_cv가 None이면 참, 아니면 d_cv ≤ limit
            """
            return piece["d_cv"] is None or piece["d_cv"] <= limit

        out.append(
            {
                "level": level,
                "cv_max": cv_max,
                "pass_pct": percent(sum(passed(piece) for piece in pieces), len(pieces)),
                "measured_pass_pct": percent(sum(value <= cv_max for value in measured), len(measured)),
                **speaker_shares(pieces, passed, up_at, down_at),
            }
        )
    return {
        "pieces": len(pieces),
        "speakers": len({piece["speaker"] for piece in pieces}),
        "no_cv_pct": percent(len(pieces) - len(measured), len(pieces)),
        "cv_median": middle,
        "cv_q1": low,
        "cv_q3": high,
        "levels": out,
    }


def detection_summary(pieces: list[dict], levels: list[list[float]]) -> list[dict]:
    """
    기기가 음절 핵을 얼마나 찾았는지에 따라 변동계수가 어떻게 달라지는지 요약함
    입력: pieces (게임 문장과 비슷한 길이의 조각 목록), levels (단계표)
    출력: 구간별 사전 목록. low·high(기기 음절 핵 수 ÷ 음절 수의 구간, high는 없으면 None), pieces(조각 수), cv_median(변동계수 중앙값), first_pass_pct·last_pass_pct(변동계수를 구한 조각 가운데 첫 단계·마지막 단계 기준 이하인 비율, %)
    알고리즘
    1. 조각마다 r(검출 비율) = D_device(기기 음절 핵 수) ÷ N(음절 수: 실제 음절 수를 알면 그것, 모르면 서버 검출 음절 수)
    2. DETECTION_BINS 구간별로 변동계수를 구한 조각만 모아 요약함
    """
    out = []
    for low, high in DETECTION_BINS:
        values = [piece["d_cv"] for piece in pieces if piece["d_cv"] is not None and low <= piece["d_nuclei"] / piece["count"] < high]
        out.append(
            {
                "low": low,
                "high": None if high == float("inf") else high,
                "pieces": len(values),
                "cv_median": quartiles(values)[0],
                "first_pass_pct": percent(sum(value <= levels[0][2] for value in values), len(values)),
                "last_pass_pct": percent(sum(value <= levels[-1][2] for value in values), len(values)),
            }
        )
    return out


def method_summary(pieces: list[dict]) -> dict:
    """
    한 무리의 조각에서 기기 방식과 서버 방식의 차이를 요약함
    입력: pieces (조각 목록. 길이로 거르지 않은 전체)
    출력: 사전. pieces(조각 수), speech_ratio_median·speech_ratio_q1·speech_ratio_q3(기기 발화 시간 ÷ 서버 발화 시간), nuclei_ratio_median(기기 음절 핵 수 ÷ 서버 검출 음절 수), level_gap_median(조각 앞 0.5초의 기기 음량 − 같은 구간의 서버 음량, dB), ambient_gap_median(말소리 주변 소음 − 서버 쪽 잡음 바닥, dB), gated_pct(그 차이가 10 dB를 넘는 조각의 비율, %. 발화 사이가 무음으로 지워진 녹음), f0_tracked_pct(서버가 유성으로 본 프레임 가운데 기기 추적기도 값을 낸 비율, %), f0_gross_pct(둘 다 값을 낸 프레임 가운데 3반음 넘게 다른 비율, %), f0_device_only_pct(기기가 값을 낸 프레임 가운데 서버는 무성으로 본 비율, %)
    알고리즘
    1. 발화 시간 비는 조각마다 구해 중앙값과 25·75백분위를 냄. 음절 핵 비는 서버 검출 음절 수가 1 이상인 조각만 씀
    2. 기본주파수 비율은 프레임 수를 모두 더해 구함
    """
    speech = [piece["d_speech_s"] / piece["speech_s"] for piece in pieces if piece["speech_s"] > 0]
    nuclei = [piece["d_nuclei"] / piece["nuclei"] for piece in pieces if piece["nuclei"]]
    level = [piece["d_lead_db"] - piece["lead_db"] for piece in pieces if piece["d_lead_db"] is not None and piece["lead_db"] is not None]
    ambient = [piece["ambient_db"] - piece["floor_db"] for piece in pieces]
    ref, both, gross, device_only = (sum(piece[key] for piece in pieces) for key in ("f0_ref", "f0_both", "f0_gross", "f0_dev_only"))
    middle, low, high = quartiles(speech)
    return {
        "pieces": len(pieces),
        "speech_ratio_median": middle,
        "speech_ratio_q1": low,
        "speech_ratio_q3": high,
        "nuclei_ratio_median": quartiles(nuclei)[0],
        "level_gap_median": quartiles(level)[0],
        "ambient_gap_median": quartiles(ambient)[0],
        "gated_pct": percent(sum(gap > 10 for gap in ambient), len(ambient)),
        "f0_tracked_pct": percent(both, ref),
        "f0_gross_pct": percent(gross, both),
        "f0_device_only_pct": percent(device_only, both + device_only),
    }


def tracking_summary(pieces: list[dict]) -> dict:
    """
    화자마다 우주선 게임의 기본주파수 추적기가 유성 구간을 얼마나 잡는지 요약함
    입력: pieces (조각 목록. 길이로 거르지 않은 전체)
    출력: 사전. speakers(서버가 유성으로 본 프레임이 있는 화자 수), tracked_median·tracked_q1·tracked_q3(화자별로 서버가 유성으로 본 프레임 가운데 기기 추적기도 값을 낸 비율의 중앙값과 25·75백분위, %), tracked_p5(같은 값의 5백분위, %), below_coverage_pct(그 비율이 SPACESHIP_MIN_COVERAGE 50% 미만인 화자의 비율, %)
    알고리즘
    1. 화자별로 f0_ref와 f0_both를 더해 비율(%)을 구함
    2. 화자별 비율의 분포와, 50% 미만인 화자의 비율을 구함
    """
    ref: dict[str, int] = defaultdict(int)
    both: dict[str, int] = defaultdict(int)
    for piece in pieces:
        ref[piece["speaker"]] += piece["f0_ref"]
        both[piece["speaker"]] += piece["f0_both"]
    shares = [100 * both[name] / ref[name] for name in ref if ref[name]]
    middle, low, high = quartiles(shares)
    return {
        "speakers": len(shares),
        "tracked_median": middle,
        "tracked_q1": low,
        "tracked_q3": high,
        "tracked_p5": float(np.percentile(shares, 5)) if shares else None,
        "below_coverage_pct": percent(sum(share < 100 * SPACESHIP_MIN_COVERAGE for share in shares), len(shares)),
    }


def rate_summary(pieces: list[dict], levels: list[list[float]], up_at: float, down_at: float) -> dict:
    """
    실제 음절 수를 아는 조각으로 말속도 허용 폭을 얼마나 넘는지 요약함
    입력: pieces (실제 음절 수를 알고 게임 문장과 비슷한 길이인 조각 목록), levels (단계표), up_at·down_at (단계를 올리고 내리는 성공률, %)
    출력: 사전. speakers(조각이 ENOUGH_PIECES 10개 이상인 화자 수), pieces(그 화자들의 조각 수), enough(화자 수가 ENOUGH_SPEAKERS 30명 이상인지), rate_median(화자별 기준 말속도의 중앙값, 음절/초), spread_median(화자별 말속도 상대 편차 중앙값의 중앙값, %),
        server_error_median·device_error_median(조각별 음절 수 오차율 |검출 − 실제| ÷ 실제의 중앙값: 서버 검출, 기기 음절 핵), server_error_30_pct(서버 오차율이 30% 이상인 조각의 비율, %),
        levels(단계별 사전 목록: level, tolerance_pct, cv_max, median_pass_pct(화자 중앙값을 기준 말속도로 둘 때 허용 폭 안인 조각 비율, %), single_pass_pct(같은 화자의 다른 조각 하나를 기준 말속도로 둘 때의 비율, %), detected_pass_pct(실제 음절 수 대신 서버 검출 음절 수로 말속도를 구했을 때의 비율, %), both_pass_pct(허용 폭과 변동계수 기준을 함께 볼 때의 비율, %), speaker_shares의 값(함께 볼 때))
    알고리즘
    1. 말속도 = N(실제 음절 수) ÷ T_speech(기기 발화 시간, 초). 게임과 같은 정의
    2. 조각이 10개 이상인 화자만 씀. 기준 말속도 = 그 화자 조각 말속도의 중앙값
    3. 상대 편차 = |말속도 ÷ 기준 말속도 − 1|. 허용 폭(%) 이하이면 통과
    4. 게임은 측정 블록 낭독 한 번으로 기준을 잡으므로, 같은 화자의 다른 조각 하나하나를 기준으로 두었을 때의 통과 비율도 구함(모든 쌍의 평균)
    5. 검출 음절 수로 구한 말속도는 검출 오차가 통과 비율을 얼마나 낮추는지 보기 위한 것임(기준 말속도도 검출 음절 수로 구한 중앙값)
    """
    by: dict[str, list[dict]] = defaultdict(list)
    for piece in pieces:
        by[piece["speaker"]].append(piece)
    kept = {name: items for name, items in by.items() if len(items) >= ENOUGH_PIECES}
    marked: list[dict] = []
    references, spreads = [], []
    pair_hits = np.zeros(len(levels))
    pairs = 0
    for items in kept.values():
        rates = np.array([item["true"] / item["d_speech_s"] for item in items])
        reference = float(np.median(rates))
        deviation = np.abs(rates / reference - 1)
        detected = np.array([(item["nuclei"] or 0) / item["d_speech_s"] for item in items])
        detected_reference = float(np.median(detected))
        detected_deviation = np.abs(detected / detected_reference - 1) if detected_reference > 0 else np.full(len(items), np.inf)
        references.append(reference)
        spreads.append(100 * float(np.median(deviation)))
        for item, own, other in zip(items, deviation, detected_deviation):
            marked.append({**item, "deviation": float(own), "detected_deviation": float(other)})
        ratio = np.abs(rates[:, None] / rates[None, :] - 1)  # [i, j]: 조각 j를 기준으로 둘 때 조각 i의 상대 편차
        off_diagonal = ~np.eye(len(rates), dtype=bool)
        pairs += int(off_diagonal.sum())
        for index, (_, tolerance, _) in enumerate(levels):
            pair_hits[index] += int((ratio[off_diagonal] <= tolerance / 100).sum())

    server_errors = [abs(item["nuclei"] - item["true"]) / item["true"] for item in marked if item["nuclei"] is not None]
    device_errors = [abs(item["d_nuclei"] - item["true"]) / item["true"] for item in marked]
    out = []
    for index, (level, tolerance, cv_max) in enumerate(levels):

        def passed(item: dict, width: float = tolerance, limit: float = cv_max) -> bool:
            """
            조각이 이 단계의 허용 폭과 변동계수 기준을 게임 규칙으로 함께 통과하는지 봄
            입력: item (상대 편차가 든 조각), width (허용 폭, %), limit (변동계수 기준)
            출력: 상대 편차가 허용 폭 이하이고, 변동계수를 구하지 못했거나 기준 이하이면 참
            알고리즘
            1. deviation ≤ width ÷ 100 이고 (d_cv가 None이거나 d_cv ≤ limit)
            """
            return item["deviation"] <= width / 100 and (item["d_cv"] is None or item["d_cv"] <= limit)

        out.append(
            {
                "level": level,
                "tolerance_pct": tolerance,
                "cv_max": cv_max,
                "median_pass_pct": percent(sum(item["deviation"] <= tolerance / 100 for item in marked), len(marked)),
                "single_pass_pct": percent(pair_hits[index], pairs),
                "detected_pass_pct": percent(sum(item["detected_deviation"] <= tolerance / 100 for item in marked), len(marked)),
                "both_pass_pct": percent(sum(passed(item) for item in marked), len(marked)),
                **speaker_shares(marked, passed, up_at, down_at),
            }
        )
    return {
        "speakers": len(kept),
        "pieces": len(marked),
        "enough": len(kept) >= ENOUGH_SPEAKERS,
        "rate_median": quartiles(references)[0],
        "spread_median": quartiles(spreads)[0],
        "server_error_median": quartiles(server_errors)[0],
        "device_error_median": quartiles(device_errors)[0],
        "server_error_30_pct": percent(sum(error >= 0.3 for error in server_errors), len(server_errors)),
        "levels": out,
    }


def build_report(rows: list[dict]) -> dict:
    """
    결과 전체를 요약함
    입력: rows (결과 파일에서 읽은 녹음별 사전 목록)
    출력: 사전. failed(실패한 녹음 수), recordings·speakers·hours(대상 녹음 수·화자 수·분량), pieces(조각 수), usable_pieces(게임 문장과 비슷한 길이의 조각 수), skipped_short(짧아서 버린 조각 수), guide_levels·up_at_pct·down_at_pct(앱의 단계표), methods(질환 → method_summary, 전체 포함), cv(질환 → cv_summary, 전체 포함), cv_by_detection(detection_summary), matching(짝 맞춤: candidates(전사 문장을 셀 수 있는 녹음 수), same_count(조각 수와 문장 수가 같은 녹음 수), matched(상관까지 통과한 녹음 수), speakers, pieces), rate(rate_summary), tracking(질환 → tracking_summary, 전체 포함). 결과가 없으면 recordings가 0이고 나머지 요약은 없음
    알고리즘
    1. 오류가 있는 줄은 실패로 세고 뺌. 단계표는 첫 결과의 것을 씀(결과마다 다르면 ValueError)
    2. 조각을 펼치고(flatten), 질환별·전체로 방식 비교와 변동계수 요약을 구함. 변동계수는 게임 문장과 비슷한 길이의 조각만 씀
    3. 실제 음절 수를 아는 조각으로 말속도 허용 폭 요약을 구함
    """
    good = [row for row in rows if not row.get("error")]
    report: dict = {"failed": len(rows) - len(good), "recordings": len(good)}
    if not good:
        return report
    tables = {json.dumps([row["guide_levels"], row["up_at_pct"], row["down_at_pct"]]) for row in good}
    if len(tables) != 1:
        raise ValueError("결과마다 앱의 단계표가 다릅니다. 같은 앱 코드로 다시 계산하세요")
    levels, up_at, down_at = good[0]["guide_levels"], good[0]["up_at_pct"], good[0]["down_at_pct"]
    pieces = flatten(good)
    usable = [piece for piece in pieces if piece["usable"]]
    groups = sorted({row["group"] for row in good})

    def named(summary: Callable[[list[dict]], dict], items: list[dict]) -> dict:
        """
        질환별과 전체로 요약을 구함
        입력: summary (조각 목록 → 요약), items (조각 목록)
        출력: 질환 이름(번호 뒤 이름) → 요약. 맨 끝에 "전체"
        알고리즘
        1. 질환 폴더별로 조각을 골라 summary를 구하고, 모든 조각으로 한 번 더 구함
        """
        out = {group.split(".", 1)[-1]: summary([item for item in items if item["group"] == group]) for group in groups}
        return {**out, "전체": summary(items)}

    candidates = [row for row in good if row.get("sentences")]
    matched = [row for row in candidates if matched_syllables(row)]
    known = [piece for piece in usable if piece["true"] is not None]
    return {
        **report,
        "speakers": len({row["speaker"] for row in good}),
        "hours": sum(row["duration_s"] for row in good) / 3600,
        "pieces": len(pieces),
        "usable_pieces": len(usable),
        "skipped_short": sum(row["skipped_short"] for row in good),
        "guide_levels": levels,
        "up_at_pct": up_at,
        "down_at_pct": down_at,
        "methods": named(method_summary, pieces),
        "cv": named(partial(cv_summary, levels=levels, up_at=up_at, down_at=down_at), usable),
        "cv_by_detection": detection_summary(usable, levels),
        "matching": {
            "candidates": len(candidates),
            "same_count": sum(len(row["sentences"]) == len(row["pieces"]) for row in candidates),
            "matched": len(matched),
            "speakers": len({row["speaker"] for row in matched}),
            "pieces": sum(len(row["pieces"]) for row in matched),
        },
        "rate": rate_summary(known, levels, up_at, down_at),
        "tracking": named(tracking_summary, pieces),
    }


def render(report: dict) -> str:
    """
    요약을 마크다운 표로 만듦
    입력: report (build_report의 결과)
    출력: 여러 줄 문자열. 머리말, 방식 비교 표, 변동계수 표 세 개(질환별, 화자별, 기기 검출 비율별), 말속도 허용 폭 표, 우주선 추적기 표
    알고리즘
    1. 결과가 없으면 "결과가 없습니다"만 돌려줌
    2. 머리말에 대상과 앱의 단계표를 적음
    3. 표마다 한 무리가 한 줄. 말속도 허용 폭은 화자 수가 모자라면 "정할 수 없음"이라고 적음
    """
    if not report["recordings"]:
        return "결과가 없습니다."
    levels = report["guide_levels"]
    names = GROUP_NAMES.values()
    order = [name for name in (*names, "전체") if name in report["cv"]] + [name for name in report["cv"] if name not in (*names, "전체")]
    lines = [
        "AI Hub 구음장애 음성(문장·문단 읽기)으로 본 게임 단계표",
        f"- 녹음 {report['recordings']}개, 화자 {report['speakers']}명, {report['hours']:.1f}시간. 계산 실패 {report['failed']}개",
        f"- 발화 조각 {report['pieces']}개(짧아서 버린 조각 {report['skipped_short']}개). 그중 게임 문장과 비슷한 길이(발화 {MIN_PIECE_SPEECH_S:g}초 이상, 음절 {GAME_SYLLABLES[0]}~{GAME_SYLLABLES[1]}개)는 {report['usable_pieces']}개",
        "- 노래방 따라 읽기 단계표(앱 코드에서 읽음): " + ", ".join(f"{level}단계 ±{tolerance:g}%·변동계수 {cv_max:g} 이하" for level, tolerance, cv_max in levels),
        f"- 게임은 라운드 성공률이 {report['up_at_pct']:g}% 이상이면 단계를 올리고 {report['down_at_pct']:g}% 이하이면 내림",
        "",
        "기기 방식(앱 코드)과 서버 방식의 차이. 조각 전체",
        "| 질환 | 조각 | 기기 발화 시간 ÷ 서버 발화 시간(중앙, 25~75%) | 기기 음절 핵 ÷ 서버 검출 음절(중앙) | 같은 구간 음량: 기기 − 서버(dB, 중앙) | 말소리 주변 소음 − 하위 10백분위(dB, 중앙) | 그 차이가 10 dB 넘는 조각 | 서버가 유성으로 본 프레임 중 기기가 잡은 비율 | 둘 다 잡은 프레임 중 3반음 넘게 다름 | 기기가 잡은 프레임 중 서버는 무성 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in order:
        item = report["methods"][name]
        lines.append(
            f"| {name} | {item['pieces']} | {number(item['speech_ratio_median'])} ({number(item['speech_ratio_q1'])}~{number(item['speech_ratio_q3'])}) | "
            f"{number(item['nuclei_ratio_median'])} | {number(item['level_gap_median'], 2)} | {number(item['ambient_gap_median'], 1)} | "
            f"{number(item['gated_pct'], 0, '%')} | {number(item['f0_tracked_pct'], 0, '%')} | "
            f"{number(item['f0_gross_pct'], 1, '%')} | {number(item['f0_device_only_pct'], 0, '%')} |"
        )

    heads = " | ".join(f"{level}단계({cv_max:g})" for level, _, cv_max in levels)
    lines += [
        "",
        "변동계수 기준을 넘는 조각의 비율. 게임 문장과 비슷한 길이의 조각. 변동계수를 구하지 못한 조각은 게임 규칙대로 통과로 셈",
        f"| 질환 | 조각 | 화자 | 변동계수를 구하지 못함 | 변동계수(중앙, 25~75%) | {heads} |",
        "|---|---|---|---|---|" + "---|" * len(levels),
    ]
    for name in order:
        item = report["cv"][name]
        cells = " | ".join(number(level["pass_pct"], 0, "%") for level in item["levels"])
        lines.append(
            f"| {name} | {item['pieces']} | {item['speakers']} | {number(item['no_cv_pct'], 0, '%')} | "
            f"{number(item['cv_median'])} ({number(item['cv_q1'])}~{number(item['cv_q3'])}) | {cells} |"
        )

    lines += [
        "",
        f"화자별로 본 변동계수 기준(조각이 {ENOUGH_PIECES}개 이상인 화자). 전체와 중풍",
        f"| 단계 | 변동계수 기준 | 무리 | 화자 | 화자별 통과 비율(중앙, 25~75%) | 통과 비율이 {report['up_at_pct']:g}% 이상인 화자 | 통과 비율이 {report['down_at_pct']:g}% 이하인 화자 | 변동계수를 구한 조각만 볼 때 통과 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, (level, _, cv_max) in enumerate(levels):
        for name in ("전체", "중풍"):
            if name not in report["cv"]:
                continue
            item = report["cv"][name]["levels"][index]
            lines.append(
                f"| {level} | {cv_max:g} | {name} | {item['speakers']} | {number(item['share_median'], 0, '%')} ({number(item['share_q1'], 0)}~{number(item['share_q3'], 0)}) | "
                f"{number(item['up_pct'], 0, '%')} | {number(item['down_pct'], 0, '%')} | {number(item['measured_pass_pct'], 0, '%')} |"
            )

    lines += [
        "",
        "기기가 음절 핵을 찾은 비율(기기 음절 핵 수 ÷ 음절 수)에 따른 변동계수. 변동계수를 구한 조각만",
        f"| 찾은 비율 | 조각 | 변동계수(중앙) | {levels[0][0]}단계 기준({levels[0][2]:g}) 이하 | {levels[-1][0]}단계 기준({levels[-1][2]:g}) 이하 |",
        "|---|---|---|---|---|",
    ]
    for item in report["cv_by_detection"]:
        label = f"{item['low']:g} 이상" if item["high"] is None else f"{item['low']:g}~{item['high']:g}"
        lines.append(
            f"| {label} | {item['pieces']} | {number(item['cv_median'])} | {number(item['first_pass_pct'], 0, '%')} | {number(item['last_pass_pct'], 0, '%')} |"
        )

    matching, rate = report["matching"], report["rate"]
    lines += [
        "",
        "말속도 허용 폭. 조각 수가 전사의 문장 수와 같아 조각마다 실제 음절 수를 아는 녹음만",
        f"- 전사 문장을 셀 수 있는 녹음 {matching['candidates']}개 가운데 조각 수 = 문장 수인 녹음 {matching['same_count']}개, "
        f"그중 조각 길이와 문장 음절 수의 상관이 {MATCH_MIN_CORR:g} 이상인 녹음 {matching['matched']}개(화자 {matching['speakers']}명, 조각 {matching['pieces']}개)",
        f"- 게임 문장과 비슷한 길이의 조각이 {ENOUGH_PIECES}개 이상인 화자 {rate['speakers']}명, 조각 {rate['pieces']}개",
    ]
    if rate["pieces"]:
        lines += [
            f"- 조각별 음절 수 오차율(중앙): 서버 검출 {number(100 * rate['server_error_median'], 0, '%')}, 기기 음절 핵 {number(100 * rate['device_error_median'], 0, '%')}. "
            f"서버 검출이 30% 이상 어긋난 조각 {number(rate['server_error_30_pct'], 0, '%')}",
            f"- 화자별 기준 말속도(중앙) {number(rate['rate_median'])}음절/초, 화자 안에서 말속도가 기준에서 벗어나는 정도(중앙) {number(rate['spread_median'], 0, '%')}",
        ]
    if not rate["enough"]:
        lines.append(f"- 화자가 {ENOUGH_SPEAKERS}명보다 적어 이 자료로는 말속도 허용 폭을 정할 수 없음. 아래 표는 참고용임")
    if rate["pieces"]:
        lines += [
            f"| 단계 | 허용 폭 | 화자 중앙값을 기준으로 | 다른 조각 하나를 기준으로 | 검출 음절 수로 구하면 | 변동계수 기준까지 함께 | 화자별 통과 비율(중앙) | {report['up_at_pct']:g}% 이상인 화자 | {report['down_at_pct']:g}% 이하인 화자 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for item in rate["levels"]:
            lines.append(
                f"| {item['level']} | ±{item['tolerance_pct']:g}% | {number(item['median_pass_pct'], 0, '%')} | {number(item['single_pass_pct'], 0, '%')} | "
                f"{number(item['detected_pass_pct'], 0, '%')} | {number(item['both_pass_pct'], 0, '%')} | {number(item['share_median'], 0, '%')} | "
                f"{number(item['up_pct'], 0, '%')} | {number(item['down_pct'], 0, '%')} |"
            )

    lines += [
        "",
        "우주선 게임의 실시간 기본주파수 추적기(참고). 서버가 유성으로 본 프레임 가운데 추적기도 값을 낸 비율을 화자별로 구함. 낭독이라 \"아\" 발성과는 다름",
        f"| 질환 | 화자 | 화자별 비율(중앙, 25~75%) | 5백분위 | {100 * SPACESHIP_MIN_COVERAGE:g}% 미만인 화자 |",
        "|---|---|---|---|---|",
    ]
    for name in order:
        item = report["tracking"][name]
        lines.append(
            f"| {name} | {item['speakers']} | {number(item['tracked_median'], 0, '%')} ({number(item['tracked_q1'], 0)}~{number(item['tracked_q3'], 0)}) | "
            f"{number(item['tracked_p5'], 0, '%')} | {number(item['below_coverage_pct'], 0, '%')} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None, out: Callable[[str], None] = print) -> int:
    """
    게임 단계표 점검 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). "run --audio 음성zip --labels 라벨zip --out 결과파일 [--group 글자] [--limit 수] [--workers 수] [--frontend 폴더]" 또는 "report 결과파일 [--json]"
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 끝남, 1 = run에서 실패한 녹음이 있음, 2 = 파일·폴더·node가 없음). 진행 상황과 표를 out으로, 입력 오류를 표준 오류로 씀. run은 결과 파일에 줄을 더함
    알고리즘
    1. 명령줄 인자를 읽음
    2. run이면 두 zip, frontend 폴더의 앱 코드와 node_modules, node가 있는지 확인하고(없으면 2) aihub.run으로 문장·문단 읽기 녹음만 계산함(compute)
    3. report면 결과 파일을 읽어(없으면 2) build_report로 요약하고, --json이면 JSON으로, 아니면 render로 출력함
    """
    parser = argparse.ArgumentParser(description="게임 단계표의 기준이 환자 음성에서 도달 가능한지 본다")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="녹음을 계산해 결과 파일에 더한다")
    run_parser.add_argument("--audio", type=Path, required=True, help="음성 zip(원천데이터)")
    run_parser.add_argument("--labels", type=Path, required=True, help="라벨 zip(라벨링데이터)")
    run_parser.add_argument("--out", type=Path, required=True, help="결과 파일(JSON Lines)")
    run_parser.add_argument("--group", help="질환 폴더 이름이 이 글자로 시작하는 것만(예: 11)")
    run_parser.add_argument("--limit", type=int, help="앞에서 이만큼만 계산한다")
    run_parser.add_argument("--workers", type=int, default=1, help="동시에 계산할 프로세스 수(기본 1)")
    run_parser.add_argument("--frontend", type=Path, default=FRONTEND, help="frontend 폴더(기본은 저장소의 frontend)")
    report_parser = commands.add_parser("report", help="결과 파일을 요약한다")
    report_parser.add_argument("results", type=Path, help="결과 파일(JSON Lines)")
    report_parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    args = parser.parse_args(argv)

    if args.command == "run":
        needed = [args.audio, args.labels, args.frontend / "src" / "games" / "guide_reading" / "analysis.ts", args.frontend / "node_modules" / "pitchy"]
        missing = [str(path) for path in needed if not path.exists()]
        if missing:
            print(f"파일이 없습니다: {', '.join(missing)}", file=sys.stderr)
            return 2
        if shutil.which("node") is None:
            print("node를 찾지 못했습니다. Node.js 24를 설치하세요", file=sys.stderr)
            return 2
        job = partial(compute, frontend=str(args.frontend.resolve()))
        return 1 if aihub.run(args.audio, args.labels, args.out, args.group, args.limit, args.workers, out, job=job, keep=keep_task) else 0

    if not args.results.is_file():
        print(f"파일이 없습니다: {args.results}", file=sys.stderr)
        return 2
    report = build_report(read_results(args.results))
    out(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
