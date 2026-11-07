"""자동 게인 조절 판정: agc_test 세션의 시험 소리 녹음 3개가 정답(fixtures/agc/signals.json)처럼 녹음됐는지 본다.

기기가 녹음 음량을 스스로 조절하면(자동 게인 조절) 음량 지표를 믿을 수 없다. 앱 녹음 경로마다 한 번 확인한다.
backend 폴더에서 실행한다. DB·S3는 .env(또는 환경 변수)의 운영 접속 정보로 읽기만 한다.
    python -m analysis.agc_check --session 세션ID          그 agc_test 세션
    python -m analysis.agc_check --user 사용자ID            그 사용자의 가장 최근 agc_test 세션
    python -m analysis.agc_check --file agc_steps=a.wav --file agc_hold=b.wav --file agc_toggle=c.wav
                                                           앱 없이 녹음한 WAV 파일
    --json을 붙이면 결과를 JSON으로 출력한다.

녹음 음량의 절대값은 재생 음량과 마이크 거리에 따라 달라지므로, 소리 안에서의 상대 변화만 본다.
판정 기준값은 모두 초기값이다.
"""

import argparse
import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.metrics.audio import HOP_S, Audio, frames, load_wav, runs
from app.schemas.common import AgcSignal
from fixtures.agc import generate as agc_fixture

SIGNALS = agc_fixture.load_signals()

ONSET_MARGIN_DB = 10  # 소리 판정: 잡음 바닥보다 이만큼 크면 소리
MIN_TONE_S = 0.3  # 이보다 짧은 소리 조각은 무시한다
# 잡음 바닥: 녹음 처음 0.5초의 음량 중앙값. 시험 소리는 모두 앞 1초가 무음이고 녹음을 먼저 시작하므로 늘 무음이다.
# 첫 소리 앞 무음 전체를 쓰지 않는 것은, 잡음에 묻힌 작은 단계가 섞여 잡음 바닥이 올라가기 때문이다.
OPENING_S = 0.5
# 잡음 바닥의 하한. 완전한 디지털 무음(합성 파일)이면 −200 dBFS가 되어, 고역 통과 필터의 아주 작은 앞 울림까지 소리로 잡힌다.
MIN_FLOOR_DBFS = -90.0
CLIP_LEVEL = 0.99  # 표본 절댓값이 이 이상이면 찌그러짐

STEP_EDGE_S = 0.5  # 단계마다 앞뒤 0.5초를 빼고 중앙값을 잰다
STEP_EXPECTED_DB = 6.0
STEP_TOLERANCE_DB = 2.0  # 단계 사이 차이가 6 ± 2 dB를 벗어나면 의심
HOLD_SKIP_S = 1.0  # 유지 소리는 앞 1초를 빼고 1초 창마다 중앙값을 잰다
HOLD_RANGE_DB = 2.0  # 1초 창 중앙값의 최대 − 최소가 이보다 크면 의심
TOGGLE_ATTACK_S = (0.03, 0.15)  # 켠 직후 구간(시험 소리의 10 ms 상승을 뺀다)
TOGGLE_TAIL_S = 0.5  # 켠 구간의 끝 쪽 구간
TOGGLE_ATTACK_DB = 2.0  # 켠 직후 − 끝 쪽 차이의 평균 절댓값이 이보다 크면 의심
TOGGLE_RANGE_DB = 2.0  # 켤 때마다의 음량(끝 쪽 중앙값) 최대 − 최소가 이보다 크면 의심
OFF_NOISE_RISE_DB = 6.0  # 끈 구간의 음량이 앞 무음보다 이만큼 크면 의심(무음에서 증폭을 올림)


@dataclass
class SignalResult:
    signal: str
    status: str  # ok | suspected | unusable | missing
    reasons: list[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)


@dataclass
class Report:
    verdict: str  # no_agc | agc_suspected | undetermined
    message: str
    signals: list[SignalResult]
    source: str


def rounded(values) -> list[float]:
    """
    값 목록을 소수 둘째 자리로 반올림함
    입력: values (숫자 목록, 단위는 그대로)
    출력: 소수 둘째 자리까지 반올림한 실수 목록
    알고리즘
    1. 값마다 실수로 바꿔 소수 둘째 자리로 반올림함
    """
    return [round(float(v), 2) for v in values]


def opening_floor(audio: Audio) -> float:
    """
    녹음 처음 0.5초의 음량 중앙값으로 잡음 바닥을 정함. 녹음이 아주 짧으면 하위 5백분위를 씀
    입력: audio (분석용 음량: 16 kHz 표본과 30 ms 프레임 음량, dBFS)
    출력: 잡음 바닥(dBFS, MIN_FLOOR_DBFS = −90 dBFS 이상)
    알고리즘
    1. 처음 OPENING_S(0.5초)에 해당하는 프레임(10 ms 간격 50개) 음량을 가져옴
    2. 프레임이 다 있으면 그 중앙값을, 녹음이 그보다 짧으면 전체 프레임 음량의 5백분위를 씀
    3. −90 dBFS보다 낮으면 −90 dBFS로 올림. 완전한 디지털 무음(합성 파일)이면 고역 통과 필터의 아주 작은 앞 울림까지 소리로 잡히기 때문임
    """
    head = audio.levels[: frames(OPENING_S)]
    floor = float(np.median(head)) if len(head) >= frames(OPENING_S) else float(np.percentile(audio.levels, 5))
    return max(floor, MIN_FLOOR_DBFS)


def tone_runs(audio: Audio, floor: float) -> list[tuple[int, int]]:
    """
    잡음 바닥보다 충분히 큰 소리 구간을 찾음
    입력: audio (분석용 음량), floor (잡음 바닥, dBFS)
    출력: 소리 구간 [시작 프레임, 끝 프레임) 목록(프레임 간격 10 ms)
    알고리즘
    1. 프레임 음량이 잡음 바닥 + ONSET_MARGIN_DB(10 dB)보다 큰 프레임이 이어진 구간을 찾음
    2. MIN_TONE_S(0.3초)보다 짧은 구간은 버림
    """
    return [(s, e) for s, e in runs(audio.levels > floor + ONSET_MARGIN_DB) if e - s >= frames(MIN_TONE_S)]


def window_median(audio: Audio, start_s: float, end_s: float) -> float | None:
    """
    정한 시간 창 안의 프레임 음량 중앙값을 구함
    입력: audio (분석용 음량), start_s (창 시작, 초), end_s (창 끝, 초, 포함하지 않음)
    출력: 프레임 음량 중앙값(dBFS). 창 안 프레임이 3개 미만이면 None
    알고리즘
    1. 프레임 중심 시각이 [start_s, end_s) 안인 프레임을 고름
    2. 3개 이상이면 그 음량의 중앙값을, 아니면 None을 돌려줌
    """
    times = audio.frame_times()
    inside = (times >= start_s) & (times < end_s)
    return float(np.median(audio.levels[inside])) if inside.sum() >= 3 else None


def clipped(raw: np.ndarray, start_s: float, end_s: float, rate: int) -> bool:
    """
    정한 시간 구간에 찌그러진(최대 크기에 닿은) 표본이 있는지 봄
    입력: raw (원래 샘플레이트의 표본별 절댓값, 채널 중 큰 값, −1~1 기준), start_s (구간 시작, 초), end_s (구간 끝, 초), rate (원래 샘플레이트, Hz)
    출력: 구간에 절댓값 CLIP_LEVEL(0.99) 이상인 표본이 있으면 참. 구간이 비면 거짓
    알고리즘
    1. 시각을 표본 번호로 바꿔 구간을 자르고, 최댓값이 0.99 이상인지 봄
    """
    part = raw[int(start_s * rate) : int(end_s * rate)]
    return bool(len(part)) and float(np.max(np.abs(part))) >= CLIP_LEVEL


def read(data: bytes) -> tuple[Audio, np.ndarray, int]:
    """
    WAV 바이트에서 분석용 음량(16 kHz)과 찌그러짐을 볼 원래 표본을 읽음
    입력: data (WAV 파일 바이트)
    출력: (분석용 음량: 모노, 16 kHz, 60 Hz 고역 통과 뒤 30 ms 프레임(10 ms 간격) 음량(dBFS), 원래 샘플레이트의 표본별 절댓값(채널 중 큰 값), 원래 샘플레이트(Hz))
    알고리즘
    1. soundfile로 32비트 실수 표본과 샘플레이트를 읽음
    2. 서버 지표 계산과 같은 전처리(load_wav)로 분석용 음량을 만듦
    3. 표본마다 채널 중 가장 큰 절댓값을 구해 함께 돌려줌
    """
    import io

    import soundfile

    raw, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return load_wav(data), np.abs(raw).max(axis=1), rate


def first_onset(audio: Audio) -> tuple[int | None, float]:
    """
    첫 소리의 시작 프레임과 잡음 바닥을 찾음
    입력: audio (분석용 음량)
    출력: (첫 소리 구간의 시작 프레임 번호(10 ms 간격), 없으면 None, 잡음 바닥(dBFS))
    알고리즘
    1. opening_floor로 잡음 바닥을 정함
    2. tone_runs로 소리 구간을 찾아 첫 구간의 시작 프레임을 돌려줌
    """
    floor = opening_floor(audio)
    found = tone_runs(audio, floor)
    return (found[0][0] if found else None), floor


def check_steps(data: bytes) -> SignalResult:
    """
    agc_steps 녹음(3초씩 6 dB 단계로 커지는 소리)에서 단계 사이 음량 차이가 그대로 녹음됐는지 판정함
    입력: data (agc_steps 녹음 WAV 바이트)
    출력: 판정 결과(SignalResult: signal agc_steps, status ok·suspected·unusable, reasons, measurements(noise_floor_dbfs 잡음 바닥 dBFS, step_levels_dbfs 단계별 음량 dBFS, increments_db 단계 사이 차이 dB))
    알고리즘
    1. 녹음을 읽고 잡음 바닥과 첫 소리를 찾음. 소리가 없으면 unusable
    2. 시간을 맞춤: 가장 큰 마지막 단계의 끝을 정답(`fixtures/agc/signals.json`)의 끝에 맞춤. 작은 첫 단계가 잡음에 묻혀도 어긋나지 않기 때문임
       - 마지막 소리 끝이 녹음 끝에서 0.2초 안이면(녹음이 소리 끝 전에 멈춤) 첫 소리 시작을 정답의 첫 단계 시작에 맞춤
    3. 단계마다 앞뒤 STEP_EDGE_S(0.5초)를 뺀 창의 음량 중앙값을 잼
       - 잴 수 없으면(녹음이 끝남), 찌그러짐이 있으면(표본 절댓값 0.99 이상), 잡음 바닥 + 10 dB보다 작으면(잡음에 묻힘) 그 단계는 쓰지 않고 이유를 적음
    4. 둘 다 쓸 수 있는 이웃 단계 사이의 음량 차이(dB)를 구함
    5. 비교할 수 있는 단계 사이가 3개 미만이면 unusable
    6. 차이가 STEP_EXPECTED_DB ± STEP_TOLERANCE_DB(6 ± 2 dB)를 벗어난 곳이 있으면 suspected, 아니면 ok
    """
    spec = SIGNALS["files"]["agc_steps"]["segments"]
    audio, raw, rate = read(data)
    onset, floor = first_onset(audio)
    result = SignalResult(AgcSignal.agc_steps.value, "ok")
    if onset is None:
        return SignalResult(result.signal, "unusable", ["소리를 찾지 못했다"])
    # 시간 맞추기: 가장 큰 마지막 단계의 끝으로 맞춘다. 작은 첫 단계가 잡음에 묻혀도 어긋나지 않는다.
    # 녹음이 소리 끝 전에 멈췄으면(끝이 녹음 끝과 붙음) 첫 소리 시작으로 맞춘다.
    last_end = max(end for _, end in tone_runs(audio, floor)) * HOP_S
    if last_end < audio.duration_s - 0.2:
        offset = last_end - spec[-1]["end_s"]
    else:
        offset = onset * HOP_S - spec[0]["start_s"]
    levels, usable, notes = [], [], []
    for index, segment in enumerate(spec):
        start, end = segment["start_s"] + offset + STEP_EDGE_S, segment["end_s"] + offset - STEP_EDGE_S
        level = window_median(audio, start, end)
        levels.append(level)
        if level is None:
            notes.append(f"{index + 1}단계: 녹음이 끝나 잴 수 없다")
            usable.append(False)
        elif clipped(raw, start, end, rate):
            notes.append(f"{index + 1}단계: 찌그러짐(재생 음량을 줄인다)")
            usable.append(False)
        elif level < floor + ONSET_MARGIN_DB:
            notes.append(f"{index + 1}단계: 잡음에 묻힘")
            usable.append(False)
        else:
            usable.append(True)
    increments = [
        (index + 1, levels[index + 1] - levels[index])
        for index in range(len(levels) - 1)
        if usable[index] and usable[index + 1]
    ]
    result.measurements = {
        "noise_floor_dbfs": round(floor, 2),
        "step_levels_dbfs": [None if v is None else round(v, 2) for v in levels],
        "increments_db": {f"{i}→{i + 1}": round(d, 2) for i, d in increments},
    }
    result.reasons = notes
    if len(increments) < 3:
        result.status = "unusable"
        result.reasons.append(f"비교할 수 있는 단계 사이가 {len(increments)}개뿐이다(3개 이상 필요)")
        return result
    off = [(i, d) for i, d in increments if abs(d - STEP_EXPECTED_DB) > STEP_TOLERANCE_DB]
    if off:
        result.status = "suspected"
        result.reasons.append(
            "단계 사이 차이가 6 ± 2 dB를 벗어남: " + ", ".join(f"{i}→{i + 1} {d:.1f} dB" for i, d in off)
        )
    return result


def check_hold(data: bytes) -> SignalResult:
    """
    agc_hold 녹음(같은 크기로 30초 유지하는 소리)에서 음량이 시간에 따라 바뀌는지 판정함
    입력: data (agc_hold 녹음 WAV 바이트)
    출력: 판정 결과(SignalResult: signal agc_hold, status ok·suspected·unusable, reasons, measurements(noise_floor_dbfs 잡음 바닥 dBFS, tone_s 소리 길이 초, second_medians_dbfs 1초 창 중앙값 dBFS, range_db 범위 dB))
    알고리즘
    1. 녹음을 읽고 잡음 바닥과 소리 구간을 찾음. 소리가 없으면 unusable
    2. 가장 긴 소리 구간에서 앞 HOLD_SKIP_S(1초)와 뒤 0.5초를 뺌
    3. 남은 구간을 1초 창으로 나눠 창마다 음량 중앙값을 잼
    4. 창이 10개 미만이면 unusable
    5. 창 중앙값의 최대 − 최소가 HOLD_RANGE_DB(2 dB)보다 크면 suspected, 아니면 ok
    """
    audio, _, _ = read(data)
    onset, floor = first_onset(audio)
    result = SignalResult(AgcSignal.agc_hold.value, "ok")
    found = tone_runs(audio, floor) if onset is not None else []
    if not found:
        return SignalResult(result.signal, "unusable", ["소리를 찾지 못했다"])
    start, end = max(found, key=lambda run: run[1] - run[0])
    start_s, end_s = start * HOP_S + HOLD_SKIP_S, end * HOP_S - STEP_EDGE_S
    medians = [m for t in np.arange(start_s, end_s - 1 + 1e-9, 1.0) if (m := window_median(audio, t, t + 1)) is not None]
    result.measurements = {"noise_floor_dbfs": round(floor, 2), "tone_s": round((end - start) * HOP_S, 2), "second_medians_dbfs": rounded(medians)}
    if len(medians) < 10:
        result.status = "unusable"
        result.reasons.append(f"유지 구간이 {len(medians)}초뿐이다(10초 이상 필요)")
        return result
    spread = max(medians) - min(medians)
    result.measurements["range_db"] = round(spread, 2)
    if spread > HOLD_RANGE_DB:
        result.status = "suspected"
        result.reasons.append(f"같은 크기 소리인데 1초 창 중앙값이 {spread:.1f} dB 범위로 바뀜(기준 {HOLD_RANGE_DB} dB)")
    return result


def check_toggle(data: bytes) -> SignalResult:
    """
    agc_toggle 녹음(1초 켜고 1초 끄기 10번)에서 켤 때·끌 때 음량이 조절되는지 판정함
    입력: data (agc_toggle 녹음 WAV 바이트)
    출력: 판정 결과(SignalResult: signal agc_toggle, status ok·suspected·unusable, reasons, measurements(noise_floor_dbfs, on_count 켠 구간 수, on_levels_dbfs 켠 구간 끝 쪽 음량, attack_minus_tail_db 켠 직후 − 끝 쪽 차이, off_levels_dbfs 끈 구간 음량, attack_mean_abs_db, on_range_db, off_rise_db))
    알고리즘
    1. 녹음을 읽고 잡음 바닥과 켠 구간을 찾음. 켠 구간이 5개 미만이면 unusable
    2. 켠 구간마다 켠 직후(시작 + 0.03~0.15초, 시험 소리의 10 ms 상승을 뺌)와 끝 쪽(끝 전 0.5초)의 음량 중앙값을 재서 차이를 구함
    3. 이웃한 켠 구간 사이(앞 끝 + 0.2초 ~ 뒤 시작 − 0.2초)의 끈 구간 음량 중앙값을 잼
    4. 켠 구간 수가 정답(10개)과 다르면 이유에 적음(판정은 바꾸지 않음)
    5. 다음 중 하나라도 맞으면 suspected, 아니면 ok
       - 켠 직후 − 끝 쪽 차이의 절댓값 평균 > TOGGLE_ATTACK_DB(2 dB)
       - 켤 때마다의 음량(끝 쪽 중앙값) 최대 − 최소 > TOGGLE_RANGE_DB(2 dB)
       - 끈 구간 음량 중앙값 − 잡음 바닥 > OFF_NOISE_RISE_DB(6 dB). 무음에서 증폭을 올렸다는 뜻임
    """
    audio, _, _ = read(data)
    onset, floor = first_onset(audio)
    result = SignalResult(AgcSignal.agc_toggle.value, "ok")
    found = tone_runs(audio, floor) if onset is not None else []
    expected = len(SIGNALS["files"]["agc_toggle"]["segments"])
    if len(found) < 5:
        return SignalResult(result.signal, "unusable", [f"켠 구간을 {len(found)}개만 찾았다(정답 {expected}개, 5개 이상 필요)"])
    attacks, tails = [], []
    for start, end in found:
        start_s, end_s = start * HOP_S, end * HOP_S
        attack = window_median(audio, start_s + TOGGLE_ATTACK_S[0], start_s + TOGGLE_ATTACK_S[1])
        tail = window_median(audio, end_s - TOGGLE_TAIL_S, end_s)
        if attack is not None and tail is not None:
            attacks.append(attack - tail)
            tails.append(tail)
    gaps = [window_median(audio, a[1] * HOP_S + 0.2, b[0] * HOP_S - 0.2) for a, b in zip(found, found[1:])]
    gaps = [g for g in gaps if g is not None]
    result.measurements = {
        "noise_floor_dbfs": round(floor, 2),
        "on_count": len(found),
        "on_levels_dbfs": rounded(tails),
        "attack_minus_tail_db": rounded(attacks),
        "off_levels_dbfs": rounded(gaps),
    }
    if len(found) != expected:
        result.reasons.append(f"켠 구간 {len(found)}개(정답 {expected}개)")
    attack_mean = float(np.mean(np.abs(attacks)))
    spread = max(tails) - min(tails)
    rise = (float(np.median(gaps)) - floor) if gaps else 0.0
    result.measurements |= {"attack_mean_abs_db": round(attack_mean, 2), "on_range_db": round(spread, 2), "off_rise_db": round(rise, 2)}
    problems = []
    if attack_mean > TOGGLE_ATTACK_DB:
        problems.append(f"켠 직후와 끝 쪽 음량 차이 평균 {attack_mean:.1f} dB(기준 {TOGGLE_ATTACK_DB} dB)")
    if spread > TOGGLE_RANGE_DB:
        problems.append(f"켤 때마다의 음량이 {spread:.1f} dB 범위로 다름(기준 {TOGGLE_RANGE_DB} dB)")
    if rise > OFF_NOISE_RISE_DB:
        problems.append(f"끈 구간의 음량이 앞 무음보다 {rise:.1f} dB 큼(무음에서 증폭을 올림, 기준 {OFF_NOISE_RISE_DB} dB)")
    if problems:
        result.status = "suspected"
        result.reasons.extend(problems)
    return result


CHECKS: dict[str, Callable[[bytes], SignalResult]] = {
    AgcSignal.agc_steps.value: check_steps,
    AgcSignal.agc_hold.value: check_hold,
    AgcSignal.agc_toggle.value: check_toggle,
}


def analyze(recordings: dict[str, Callable[[], bytes]], source: str) -> Report:
    """
    시험 소리 녹음 3개를 판정해 자동 게인 조절이 있는지 결론을 냄
    입력: recordings (agc_signal → 녹음 바이트를 읽는 함수), source (보고에 적을 녹음 출처 설명)
    출력: 보고(Report: verdict no_agc·agc_suspected·undetermined, message, 소리별 결과, source)
    알고리즘
    1. agc_steps·agc_hold·agc_toggle마다 녹음이 없으면 missing으로 보고함
    2. 있으면 녹음을 읽어 그 소리의 판정 함수로 판정함. 예외가 나면(읽을 수 없는 파일 등) unusable로 보고함
    3. 하나라도 suspected면 agc_suspected(이 녹음 경로의 음량 지표는 믿기 어려움)
    4. 아니고 missing·unusable이 있으면 undetermined(그 소리를 다시 녹음함)
    5. 모두 ok면 no_agc
    """
    results = []
    for signal, check in CHECKS.items():
        if signal not in recordings:
            results.append(SignalResult(signal, "missing", ["이 시험 소리의 녹음이 없다"]))
            continue
        try:
            results.append(check(recordings[signal]()))
        except Exception as error:  # 읽을 수 없는 파일 등
            results.append(SignalResult(signal, "unusable", [f"녹음을 분석하지 못했다: {error}"]))
    statuses = {r.signal: r.status for r in results}
    suspected = [s for s, status in statuses.items() if status == "suspected"]
    incomplete = [s for s, status in statuses.items() if status in ("missing", "unusable")]
    if suspected:
        verdict, message = "agc_suspected", f"자동 게인 조절 의심: {', '.join(suspected)}. 이 녹음 경로의 음량 지표는 믿기 어렵다"
    elif incomplete:
        verdict, message = "undetermined", f"판정 불가: {', '.join(incomplete)}를 다시 녹음한다"
    else:
        verdict, message = "no_agc", "자동 게인 조절이 보이지 않는다(시험 소리 3개 모두 정상)"
    return Report(verdict, message, results, source)


def session_recordings(db, storage, session_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None):
    """
    agc_test 세션과 그 녹음(agc_signal별로 가장 나중에 올린 것)을 찾음. 업로드가 확인된(complete) 녹음만 씀
    입력: db (DB 세션), storage (녹음 버킷 저장소), session_id (agc_test 세션 ID, 선택), user_id (사용자 ID, session_id가 없을 때 그 사용자의 가장 최근 agc_test 세션)
    출력: (세션 행, agc_signal → S3에서 녹음 바이트를 읽는 함수 사전). 세션이 없으면 (None, 빈 사전). DB·S3는 읽기만 함
    알고리즘
    1. 목적이 agc_test인 세션 중 session_id가 있으면 그 세션을, 없으면 그 사용자의 시작 시각이 가장 늦은 세션을 하나 고름
    2. 그 세션의 agc_tone 녹음 중 업로드 확인 시각(uploaded_at)이 있는 행을 만든 시각 순서로 읽음
    3. agc_signal마다 가장 나중에 만든 행이 남도록 묶고, S3 읽기는 판정할 때 하도록 함수로 넘김
    """
    from sqlalchemy import select

    from app.db.models import RecordingRow, SessionRow
    from app.schemas.common import Purpose, RecordingKind

    query = select(SessionRow).where(SessionRow.purpose == Purpose.agc_test)
    if session_id is not None:
        query = query.where(SessionRow.id == session_id)
    else:
        query = query.where(SessionRow.user_id == user_id).order_by(SessionRow.started_at.desc())
    session = db.scalars(query.limit(1)).first()
    if session is None:
        return None, {}
    rows = db.scalars(
        select(RecordingRow)
        .where(RecordingRow.session_id == session.id, RecordingRow.kind == RecordingKind.agc_tone, RecordingRow.uploaded_at.is_not(None))
        .order_by(RecordingRow.created_at)
    ).all()
    return session, {row.agc_signal: (lambda key=row.s3_key: storage.read(key)) for row in rows}


def render(report: Report) -> str:
    """
    판정 보고를 사람이 읽는 글로 바꿈
    입력: report (판정 보고)
    출력: 여러 줄 문자열(제목과 출처, 결과 문장, 소리별 상태·이유·측정값)
    알고리즘
    1. 제목, 결과 문장, 빈 줄을 넣음
    2. 소리마다 "- 소리: 상태(정상·의심·판정 불가·녹음 없음)" 줄, 이유 줄, 측정값 줄을 들여 써서 넣음
    """
    names = {"ok": "정상", "suspected": "의심", "unusable": "판정 불가", "missing": "녹음 없음"}
    lines = [f"자동 게인 조절 판정 ({report.source})", f"결과: {report.message}", ""]
    for result in report.signals:
        lines.append(f"- {result.signal}: {names[result.status]}")
        lines.extend(f"    {reason}" for reason in result.reasons)
        for key, value in result.measurements.items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines)


def parse_file(value: str) -> tuple[str, Path]:
    """
    명령줄의 --file 값 "소리=WAV 경로"를 소리 이름과 경로로 나눔
    입력: value (명령줄 값, 예: agc_steps=a.wav)
    출력: (소리 이름, 파일 경로). 소리 이름이 agc_steps·agc_hold·agc_toggle이 아니거나 경로가 비면 argparse.ArgumentTypeError
    알고리즘
    1. 첫 = 앞뒤로 나눠 소리 이름과 경로를 검사하고 돌려줌
    """
    signal, _, path = value.partition("=")
    if signal not in CHECKS or not path:
        raise argparse.ArgumentTypeError(f"'agc_steps=파일' 형식이어야 합니다(소리: {', '.join(CHECKS)})")
    return signal, Path(path)


def main(argv: list[str] | None = None, db=None, storage=None, out: Callable[[str], None] = print) -> int:
    """
    자동 게인 조절 시험 녹음을 판정하는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --session(agc_test 세션 ID), --user(사용자 ID, 가장 최근 agc_test 세션), --file(소리=WAV 파일, 소리마다 한 번씩) 중 하나는 꼭 줌. --json(JSON으로 출력)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - storage: 녹음 버킷 저장소(시험용 주입, 없으면 환경 변수·backend/.env의 AWS_*·S3_BUCKET으로 만듦)
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 판정함, 2 = 파일이 없거나 agc_test 세션을 찾지 못함). 판정 결과를 out으로, 오류를 표준 오류로 씀. DB·S3는 읽기만 함
    알고리즘
    1. 명령줄 인자를 읽음
    2. --file이면 파일이 모두 있는지 확인하고(없으면 2), 파일을 읽는 함수로 녹음을 모음
    3. 아니면 DB·S3에 연결해 session_recordings로 세션과 녹음을 찾음. 세션이 없으면 2
    4. analyze로 판정하고, --json이면 JSON으로, 아니면 render로 출력한 뒤 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="자동 게인 조절 시험 녹음을 판정한다")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session", type=uuid.UUID, help="agc_test 세션 ID")
    source.add_argument("--user", type=uuid.UUID, help="사용자 ID(가장 최근 agc_test 세션)")
    source.add_argument("--file", type=parse_file, action="append", help="소리=WAV 파일. 소리마다 한 번씩")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    args = parser.parse_args(argv)

    if args.file:
        missing = [path for _, path in args.file if not path.is_file()]
        if missing:
            print(f"파일이 없습니다: {', '.join(map(str, missing))}", file=sys.stderr)
            return 2
        recordings = {signal: path.read_bytes for signal, path in args.file}
        label = "파일"
    else:
        if db is None or storage is None:
            from sqlalchemy.orm import Session

            from app.db.engine import get_engine
            from app.storage.s3 import get_storage

            db = db or Session(get_engine())
            storage = storage or get_storage()
        session, recordings = session_recordings(db, storage, args.session, args.user)
        if session is None:
            print("agc_test 세션을 찾지 못했습니다", file=sys.stderr)
            return 2
        label = f"세션 {session.id}, 시작 {session.started_at:%Y-%m-%d %H:%M}, 기기 {session.device_model}, 입력 {session.input_device}"

    report = analyze(recordings, label)
    out(json.dumps(asdict(report), ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
