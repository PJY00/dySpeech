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
    return [round(float(v), 2) for v in values]


def opening_floor(audio: Audio) -> float:
    """처음 0.5초의 음량 중앙값. 녹음이 아주 짧으면 하위 5백분위"""
    head = audio.levels[: frames(OPENING_S)]
    floor = float(np.median(head)) if len(head) >= frames(OPENING_S) else float(np.percentile(audio.levels, 5))
    return max(floor, MIN_FLOOR_DBFS)


def tone_runs(audio: Audio, floor: float) -> list[tuple[int, int]]:
    return [(s, e) for s, e in runs(audio.levels > floor + ONSET_MARGIN_DB) if e - s >= frames(MIN_TONE_S)]


def window_median(audio: Audio, start_s: float, end_s: float) -> float | None:
    times = audio.frame_times()
    inside = (times >= start_s) & (times < end_s)
    return float(np.median(audio.levels[inside])) if inside.sum() >= 3 else None


def clipped(raw: np.ndarray, start_s: float, end_s: float, rate: int) -> bool:
    part = raw[int(start_s * rate) : int(end_s * rate)]
    return bool(len(part)) and float(np.max(np.abs(part))) >= CLIP_LEVEL


def read(data: bytes) -> tuple[Audio, np.ndarray, int]:
    """분석용 음량(16 kHz)과, 찌그러짐을 볼 원래 표본"""
    import io

    import soundfile

    raw, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return load_wav(data), np.abs(raw).max(axis=1), rate


def first_onset(audio: Audio) -> tuple[int | None, float]:
    """첫 소리의 시작 프레임과 잡음 바닥"""
    floor = opening_floor(audio)
    found = tone_runs(audio, floor)
    return (found[0][0] if found else None), floor


def check_steps(data: bytes) -> SignalResult:
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
    """recordings: agc_signal → 녹음을 읽는 함수. 빠진 소리는 missing으로 보고한다."""
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
    """agc_test 세션과 그 녹음(agc_signal별로 가장 나중에 올린 것). 업로드가 확인된(complete) 녹음만 쓴다."""
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
    names = {"ok": "정상", "suspected": "의심", "unusable": "판정 불가", "missing": "녹음 없음"}
    lines = [f"자동 게인 조절 판정 ({report.source})", f"결과: {report.message}", ""]
    for result in report.signals:
        lines.append(f"- {result.signal}: {names[result.status]}")
        lines.extend(f"    {reason}" for reason in result.reasons)
        for key, value in result.measurements.items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines)


def parse_file(value: str) -> tuple[str, Path]:
    signal, _, path = value.partition("=")
    if signal not in CHECKS or not path:
        raise argparse.ArgumentTypeError(f"'agc_steps=파일' 형식이어야 합니다(소리: {', '.join(CHECKS)})")
    return signal, Path(path)


def main(argv: list[str] | None = None, db=None, storage=None, out: Callable[[str], None] = print) -> int:
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
