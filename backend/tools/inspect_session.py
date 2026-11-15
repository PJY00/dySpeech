"""실제 앱이 보낸 세션을 DB와 S3에서 꺼내, API 명세(docs/api.md)와 어긋나는 점을 찾는다.

요청을 받을 때 서버가 막는 것(형식, 단위, 빠진 필드)은 이미 걸러지므로, 여기서는 요청은 통과했지만
명세의 세션 구조·녹음 규격과 다른 것, 그리고 확인이 필요한 것을 본다.
backend 폴더에서 실행한다. DB·S3는 .env(또는 환경 변수)의 운영 접속 정보로 읽기만 한다.
    python -m tools.inspect_session --session 세션ID
    python -m tools.inspect_session --user 사용자ID
    python -m tools.inspect_session --all --since 2026-11-20      그날(한국 시간) 0시 이후 시작한 세션
    --no-s3    녹음 파일은 보지 않고 DB에 있는 것만 점검한다
    --json     JSON으로 출력한다

- [위반]: API 명세와 어긋난 것. 앱 쪽을 고쳐야 한다(5장 녹음 규격, 9장 세션 구조).
- [주의]: 명세 위반은 아니지만 확인할 것(기기 시계, 서버가 못 구한 지표 등).
- 길이·시각을 보는 기준값은 모두 초기값이다. 시각은 한국 시간(UTC+9)으로 보여 준다.
"""

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.metrics import METRICS
from app.db.models import GameRunRow, MeasurementRow, MeasurementSource, RecordingRow, SessionRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import AgcSignal, Purpose, RecordingKind, SessionStatus
from app.schemas.recordings import MAX_DURATION_S
from app.storage.s3 import RecordingStorage
from app.storage.wav import WAV_HEADER_BYTES, WavHeaderError, format_problems, parse_wav_header

VIOLATION = "위반"
CAUTION = "주의"
KST = timezone(timedelta(hours=9), "KST")  # 화면에 보여 주는 시각과 --since의 기준

MEASURED = (Purpose.training, Purpose.retest)  # 측정 블록을 하는 세션
MPT_TRIALS = (2, 3)  # 세션 하나의 "아" 녹음 수(최소, 최대)
DURATION_TOLERANCE_S = 0.5  # 알린 길이와 실제 길이가 이만큼(또는 5%) 넘게 다르면 위반
DURATION_TOLERANCE_RATIO = 0.05
NOISE_FLOOR_S = (1.0, 5.0)  # "조용히 약 2초" 녹음으로 볼 수 있는 길이 범위
CLOCK_AHEAD = timedelta(minutes=10)  # 시작 시각이 서버가 세션을 받은 시각보다 이만큼 넘게 늦으면 기기 시계를 의심
LONG_SESSION = timedelta(hours=24)
GAME_TIME_MARGIN = timedelta(minutes=1)  # 라운드 시작 시각이 세션 시간 밖으로 벗어나도 봐주는 폭


@dataclass(frozen=True)
class Finding:
    level: str  # VIOLATION 또는 CAUTION
    code: str
    message: str
    recording_id: str | None = None


@dataclass
class Bundle:
    session: SessionRow
    recordings: list[RecordingRow]
    device: list[MeasurementRow]
    server: list[MeasurementRow]  # 지금 알고리즘 버전의 서버 값
    game_runs: list[GameRunRow]
    is_first_measured: bool  # 이 사용자의 측정 세션(training·retest) 중 complete까지 간 첫 세션인지

    def of_kind(self, kind: RecordingKind) -> list[RecordingRow]:
        """
        주어진 종류의 녹음만 고름
        입력: kind (녹음 종류)
        출력: 그 종류의 녹음 행 목록(읽은 순서 그대로)
        알고리즘
        1. recordings에서 kind가 같은 행만 모음
        """
        return [recording for recording in self.recordings if recording.kind is kind]


@dataclass
class Report:
    session_id: str
    user_id: str
    purpose: str
    status: str
    started_at: str
    device_model: str
    os_version: str
    app_version: str
    input_device: str
    dev: bool
    recordings: dict[str, int]
    device_measurements: int
    server_measurements: int
    game_runs: dict[str, int]
    findings: list[Finding] = field(default_factory=list)

    def count(self, level: str) -> int:
        """
        주어진 수준의 점검 결과 수를 셈
        입력: level (VIOLATION 또는 CAUTION)
        출력: 그 수준의 Finding 수
        알고리즘
        1. findings에서 level이 같은 것을 셈
        """
        return sum(finding.level == level for finding in self.findings)


def load(db: Session, session: SessionRow) -> Bundle:
    """
    세션 하나를 점검하는 데 필요한 행을 읽음. 읽기만 함
    입력: db (DB 세션), session (점검할 세션 행)
    출력: Bundle(세션, 녹음(만든 시각·ID 순), 기기 측정값, 지금 알고리즘 버전의 서버 측정값, 게임 기록(시작 시각 순), 이 사용자의 첫 측정 세션인지)
    알고리즘
    1. 이 세션의 녹음, 측정값, 게임 기록을 읽음. 서버 측정값은 알고리즘 버전이 지금 버전(ALGORITHM_VERSION)인 것만 둠
    2. 이 사용자의 training·retest 세션 중 open이 아닌(complete까지 간) 세션을 시작 시각·ID 순으로 보아, 가장 이른 것이 이 세션인지 확인함
    """
    recordings = list(
        db.scalars(
            select(RecordingRow).where(RecordingRow.session_id == session.id).order_by(RecordingRow.created_at, RecordingRow.id)
        )
    )
    measurements = list(db.scalars(select(MeasurementRow).where(MeasurementRow.session_id == session.id)))
    game_runs = list(
        db.scalars(select(GameRunRow).where(GameRunRow.session_id == session.id).order_by(GameRunRow.started_at, GameRunRow.id))
    )
    first = db.scalar(
        select(SessionRow.id)
        .where(
            SessionRow.user_id == session.user_id,
            SessionRow.purpose.in_(MEASURED),
            SessionRow.status != SessionStatus.open,
        )
        .order_by(SessionRow.started_at, SessionRow.id)
        .limit(1)
    )
    return Bundle(
        session=session,
        recordings=recordings,
        device=[m for m in measurements if m.source is MeasurementSource.device],
        server=[m for m in measurements if m.source is MeasurementSource.server and m.algorithm_version == ALGORITHM_VERSION],
        game_runs=game_runs,
        is_first_measured=first == session.id,
    )


def check_structure(bundle: Bundle) -> list[Finding]:
    """
    세션에 들어 있는 녹음 종류와 개수가 API 명세의 세션 구조(5.2, 9장)와 맞는지 봄
    입력: bundle (load로 읽은 세션 자료. complete까지 간 세션)
    출력: Finding 목록
    알고리즘
    1. agc_test 세션: agc_tone이 아닌 녹음이 있으면 위반, 시험 소리 3개(agc_steps, agc_hold, agc_toggle)가 하나씩이 아니면 위반, 게임 기록이 있으면 위반
    2. training·retest 세션: agc_tone 녹음이 있으면 위반
    3. noise_floor가 1개가 아니면 위반, mpt_trial이 2~3개가 아니면 위반, mpt_trial의 trial_no가 1부터 차례가 아니면 위반, eval_sentence가 1개가 아니면 위반
    4. calibration_glide가 2개 이상이면 위반. 이 사용자의 첫 측정 세션인데 없으면 위반. 첫 측정 세션이 아닌데 있으면 주의(서버는 새 값을 씀)
    5. retest 세션에 게임 기록이나 game 녹음이 있으면 위반
    6. game 녹음의 game_run_id가 이 세션의 게임 기록에 없으면 위반
    """
    session = bundle.session
    findings: list[Finding] = []

    def violation(code: str, message: str, recording: RecordingRow | None = None) -> None:
        """
        위반 하나를 목록에 더함
        입력: code (점검 항목 이름), message (설명), recording (관련 녹음 행, 선택)
        출력: 없음 (findings에 Finding을 더함)
        알고리즘
        1. 수준 VIOLATION으로 Finding을 만들어 더함
        """
        findings.append(Finding(VIOLATION, code, message, str(recording.id) if recording else None))

    if session.purpose is Purpose.agc_test:
        others = Counter(r.kind.value for r in bundle.recordings if r.kind is not RecordingKind.agc_tone)
        if others:
            violation("KIND_NOT_ALLOWED", f"자동 게인 조절 시험 세션에 다른 종류의 녹음이 있음: {dict(others)}")
        signals = Counter(r.agc_signal for r in bundle.of_kind(RecordingKind.agc_tone))
        expected = {signal.value: 1 for signal in AgcSignal}
        if dict(signals) != expected:
            violation("AGC_SIGNALS", f"시험 소리 3개가 하나씩 있어야 함. 받은 것: {dict(signals)}")
        if bundle.game_runs:
            violation("GAME_RUNS_NOT_ALLOWED", f"자동 게인 조절 시험 세션에 게임 기록이 {len(bundle.game_runs)}개 있음")
        return findings

    agc = bundle.of_kind(RecordingKind.agc_tone)
    if agc:
        violation("KIND_NOT_ALLOWED", f"{session.purpose.value} 세션에 agc_tone 녹음이 {len(agc)}개 있음")
    noise = bundle.of_kind(RecordingKind.noise_floor)
    if len(noise) != 1:
        violation("NOISE_FLOOR_COUNT", f"noise_floor 녹음이 {len(noise)}개임. 세션마다 1개여야 함")
    trials = bundle.of_kind(RecordingKind.mpt_trial)
    if not MPT_TRIALS[0] <= len(trials) <= MPT_TRIALS[1]:
        violation("MPT_TRIAL_COUNT", f"mpt_trial 녹음이 {len(trials)}개임. 세션마다 {MPT_TRIALS[0]}~{MPT_TRIALS[1]}개여야 함")
    numbers = sorted(r.trial_no for r in trials if r.trial_no is not None)
    if trials and numbers != list(range(1, len(trials) + 1)):
        violation("MPT_TRIAL_NO", f"mpt_trial의 trial_no가 1부터 차례가 아님: {numbers}")
    sentences = bundle.of_kind(RecordingKind.eval_sentence)
    if len(sentences) != 1:
        violation("EVAL_SENTENCE_COUNT", f"eval_sentence 녹음이 {len(sentences)}개임. 세션마다 1개여야 함")

    glides = bundle.of_kind(RecordingKind.calibration_glide)
    if len(glides) > 1:
        violation("CALIBRATION_COUNT", f"calibration_glide 녹음이 {len(glides)}개임. 1개여야 함")
    elif bundle.is_first_measured and not glides:
        violation("CALIBRATION_MISSING", "이 사용자의 첫 측정 세션인데 calibration_glide 녹음이 없음")
    elif not bundle.is_first_measured and glides:
        findings.append(Finding(CAUTION, "CALIBRATION_LATER", "첫 측정 세션이 아닌데 calibration_glide 녹음이 있음. 서버는 새 값을 씀", str(glides[0].id)))

    games = bundle.of_kind(RecordingKind.game)
    if session.purpose is Purpose.retest:
        if bundle.game_runs:
            violation("GAME_RUNS_NOT_ALLOWED", f"반복 측정 세션에 게임 기록이 {len(bundle.game_runs)}개 있음")
        if games:
            violation("KIND_NOT_ALLOWED", f"반복 측정 세션에 game 녹음이 {len(games)}개 있음")
    known = {run.id for run in bundle.game_runs}
    for recording in games:
        if recording.game_run_id not in known:
            violation("GAME_RECORDING_UNLINKED", f"game 녹음의 game_run_id({recording.game_run_id})가 이 세션의 게임 기록에 없음", recording)
    return findings


def check_times(bundle: Bundle) -> list[Finding]:
    """
    세션의 시작·끝 시각이 말이 되는지 봄
    입력: bundle (load로 읽은 세션 자료)
    출력: Finding 목록
    알고리즘
    1. 끝난 시각이 시작 시각보다 이르면 위반
    2. 세션 길이가 LONG_SESSION(24시간)을 넘으면 주의
    3. 시작 시각이 서버가 세션을 받은 시각(created_at)보다 CLOCK_AHEAD(10분) 넘게 늦으면 주의(기기 시계가 빠름)
    """
    session = bundle.session
    findings = []
    if session.ended_at is not None:
        if session.ended_at < session.started_at:
            findings.append(Finding(VIOLATION, "ENDED_BEFORE_STARTED", f"끝난 시각({session.ended_at.astimezone(KST):%Y-%m-%d %H:%M})이 시작 시각보다 이름"))
        elif session.ended_at - session.started_at > LONG_SESSION:
            hours = (session.ended_at - session.started_at).total_seconds() / 3600
            findings.append(Finding(CAUTION, "LONG_SESSION", f"세션 길이가 {hours:.1f}시간임"))
    if session.started_at - session.created_at > CLOCK_AHEAD:
        minutes = (session.started_at - session.created_at).total_seconds() / 60
        findings.append(Finding(CAUTION, "CLOCK", f"시작 시각이 서버가 세션을 받은 시각보다 {minutes:.0f}분 늦음. 기기 시계를 확인할 것"))
    return findings


def check_records(bundle: Bundle) -> list[Finding]:
    """
    complete 때 함께 온 기기 지표와 게임 기록을 봄
    입력: bundle (load로 읽은 세션 자료. complete까지 간 세션)
    출력: Finding 목록(모두 주의)
    알고리즘
    1. training·retest 세션에 기기 지표가 하나도 없으면 주의(선택 항목이지만 난이도 기준값 복구에 쓰임)
    2. 게임마다 round_index가 1부터 차례(겹침·빠짐 없음)가 아니면 주의
    3. 라운드 시작 시각이 세션 시작~끝(앞뒤 GAME_TIME_MARGIN 1분까지)을 벗어나면 주의
    4. 게임 기록이 2개 이상이고 success_rate_pct가 모두 1 이하이며 정수가 아닌 값이 있으면 주의(0~1 비율로 보낸 것으로 보임)
    """
    session = bundle.session
    findings = []
    if session.purpose in MEASURED and not bundle.device:
        findings.append(Finding(CAUTION, "NO_DEVICE_MEASUREMENTS", "기기 지표(device_measurements)가 없음. 난이도 기준값 복구에 쓰이는 값임"))
    rounds: dict[str, list[int]] = {}
    for run in bundle.game_runs:
        rounds.setdefault(run.game, []).append(run.round_index)
    for game, indexes in rounds.items():
        if sorted(indexes) != list(range(1, len(indexes) + 1)):
            findings.append(Finding(CAUTION, "ROUND_INDEX", f"{game}의 round_index가 1부터 차례가 아님: {sorted(indexes)}"))
    low = session.started_at - GAME_TIME_MARGIN
    high = session.ended_at + GAME_TIME_MARGIN if session.ended_at is not None else None
    outside = [run for run in bundle.game_runs if run.started_at < low or (high is not None and run.started_at > high)]
    if outside:
        findings.append(Finding(CAUTION, "GAME_RUN_TIME", f"라운드 시작 시각이 세션 시간 밖인 게임 기록이 {len(outside)}개 있음"))
    rates = [run.success_rate_pct for run in bundle.game_runs]
    if len(rates) >= 2 and max(rates) <= 1 and any(rate != int(rate) for rate in rates):
        findings.append(Finding(CAUTION, "SUCCESS_RATE_SCALE", f"success_rate_pct가 모두 1 이하임({rates}). 0~100(%)이 아니라 0~1 비율로 보낸 것으로 보임"))
    return findings


def check_server(bundle: Bundle) -> list[Finding]:
    """
    서버 지표 계산이 끝난 모습을 봄
    입력: bundle (load로 읽은 세션 자료. complete까지 간 세션)
    출력: Finding 목록(모두 주의)
    알고리즘
    1. failed면 오류 코드를 적어 주의
    2. done인 training·retest 세션에서, 지표 정의(METRICS) 중 지금 알고리즘 버전 서버 값이 없는 지표가 있으면 그 이름을 적어 주의
    3. 서버 값 중 신뢰 낮음이 있으면 그 이름을 적어 주의
    """
    session = bundle.session
    findings = []
    if session.status is SessionStatus.failed:
        code = (session.error or {}).get("code", "알 수 없음")
        findings.append(Finding(CAUTION, "FAILED", f"서버 지표 계산이 실패함(오류 코드 {code})"))
    if session.status is SessionStatus.done and session.purpose in MEASURED:
        present = {measurement.metric for measurement in bundle.server}
        missing = [metric.value for metric in METRICS if metric.value not in present]
        if missing:
            findings.append(Finding(CAUTION, "MISSING_SERVER_METRICS", f"서버가 구하지 못한 지표: {', '.join(missing)}"))
    low = [measurement.metric for measurement in bundle.server if measurement.low_reliability]
    if low:
        findings.append(Finding(CAUTION, "LOW_RELIABILITY", f"서버 값이 신뢰 낮음인 지표: {', '.join(sorted(low))}"))
    return findings


def data_start(head: bytes) -> int | None:
    """
    WAV 앞부분에서 소리 데이터(data 청크 본문)가 시작하는 위치를 찾음
    입력: head (WAV 파일 앞부분, 바이트. RIFF/WAVE 머리글이 있는 것)
    출력: data 청크 본문의 시작 위치(바이트). 앞부분 안에서 찾지 못하면 None
    알고리즘
    1. 12바이트 위치부터 청크를 차례로 봄(청크 이름 4바이트 + 크기 4바이트, 리틀 엔디언)
    2. 이름이 data면 본문 시작 위치를 돌려줌
    3. 다른 청크면 크기만큼 건너뜀. 크기가 홀수면 1바이트 더 건너뜀
    """
    offset = 12
    while offset + 8 <= len(head):
        size = int.from_bytes(head[offset + 4 : offset + 8], "little")
        if head[offset : offset + 4] == b"data":
            return offset + 8
        offset += 8 + size + size % 2
    return None


def check_files(bundle: Bundle, storage: RecordingStorage, now: datetime) -> list[Finding]:
    """
    S3에 올라온 녹음 파일이 알린 내용·녹음 규격(5.1)과 맞는지 봄. 읽기만 함
    입력: bundle (load로 읽은 세션 자료. complete까지 간 세션), storage (녹음 저장소, S3), now (지금 시각. 보관 기한이 지났는지 볼 때 씀)
    출력: Finding 목록. 녹음마다 문제가 있으면 recording_id를 붙임
    알고리즘
    1. 녹음마다 S3 객체 정보를 읽음. 없으면 보관 기한(retain_until)이 지났으면 주의, 아니면 위반으로 적고 다음 녹음으로 넘어감
    2. 객체 크기가 알린 size_bytes와 다르면 위반
    3. 앞 64 KB를 읽어 WAV 형식을 봄. 머리글을 읽지 못하거나 규격(모노, 16비트 PCM, 허용 샘플레이트, 알린 샘플레이트와 같음)과 다르면 위반으로 적고 다음 녹음으로 넘어감
    4. 실제 길이 T(초) = (객체 크기 − 소리 데이터 시작 위치) ÷ (샘플레이트 × 채널 수 × 표본당 바이트 수)를 구함
    5. T가 MAX_DURATION_S(120초)를 넘으면 위반. T와 알린 duration_s의 차이가 0.5초와 알린 길이의 5% 중 큰 값을 넘으면 위반
    6. noise_floor 녹음의 T가 NOISE_FLOOR_S(1~5초) 밖이면 주의
    7. 녹음의 샘플레이트가 세션 등록 때 알린 샘플레이트와 다르면 주의
    """
    findings = []
    for recording in bundle.recordings:
        identifier = str(recording.id)
        label = recording.kind.value
        info = storage.info(recording.s3_key)
        if info is None:
            if recording.retain_until is not None and recording.retain_until < now:
                findings.append(Finding(CAUTION, "OBJECT_EXPIRED", f"{label} 녹음 파일이 없음(보관 기한 {recording.retain_until.astimezone(KST):%Y-%m-%d}이 지남)", identifier))
            else:
                findings.append(Finding(VIOLATION, "OBJECT_MISSING", f"{label} 녹음 파일이 S3에 없음", identifier))
            continue
        if info.size != recording.size_bytes:
            findings.append(Finding(VIOLATION, "SIZE_MISMATCH", f"{label} 파일 크기 {info.size}바이트가 알린 size_bytes {recording.size_bytes}와 다름", identifier))
        head = storage.read_start(recording.s3_key, WAV_HEADER_BYTES)
        try:
            fmt = parse_wav_header(head)
        except WavHeaderError as error:
            findings.append(Finding(VIOLATION, "WAV_FORMAT", f"{label}: {error}", identifier))
            continue
        problems = format_problems(fmt, recording.sample_rate)
        if problems:
            findings.append(Finding(VIOLATION, "WAV_FORMAT", f"{label}: {' / '.join(problems)}", identifier))
            continue
        start = data_start(head)
        if start is not None:
            seconds = (info.size - start) / (fmt.sample_rate * fmt.channels * fmt.bits_per_sample // 8)
            if seconds > MAX_DURATION_S:
                findings.append(Finding(VIOLATION, "DURATION_TOO_LONG", f"{label} 길이가 {seconds:.1f}초임. 파일 하나는 {MAX_DURATION_S}초까지임", identifier))
            tolerance = max(DURATION_TOLERANCE_S, DURATION_TOLERANCE_RATIO * recording.duration_s)
            if abs(seconds - recording.duration_s) > tolerance:
                findings.append(Finding(VIOLATION, "DURATION_MISMATCH", f"{label} 실제 길이 {seconds:.2f}초가 알린 duration_s {recording.duration_s:.2f}초와 다름", identifier))
            if recording.kind is RecordingKind.noise_floor and not NOISE_FLOOR_S[0] <= seconds <= NOISE_FLOOR_S[1]:
                findings.append(Finding(CAUTION, "NOISE_FLOOR_LENGTH", f"noise_floor 길이가 {seconds:.1f}초임. 조용히 약 2초 녹음하는 항목임", identifier))
        if recording.sample_rate != bundle.session.sample_rate:
            findings.append(Finding(CAUTION, "SAMPLE_RATE_DIFFERS", f"{label} 샘플레이트 {recording.sample_rate} Hz가 세션의 {bundle.session.sample_rate} Hz와 다름", identifier))
    return findings


def inspect(db: Session, storage: RecordingStorage | None, session: SessionRow, now: datetime) -> Report:
    """
    세션 하나를 점검해 결과를 모음. 읽기만 함
    입력: db (DB 세션), storage (녹음 저장소, S3. None이면 파일 점검을 건너뜀), session (점검할 세션 행), now (지금 시각)
    출력: Report(세션 정보(시작 시각은 한국 시간 ISO 8601), 종류별 녹음 수, 기기 지표 수, 서버 지표 수, 게임별 기록 수, Finding 목록)
    알고리즘
    1. 세션 자료를 읽음(load)
    2. 세션이 아직 open(complete 전)이면 그 사실만 주의로 적고, 시각 점검만 함
    3. 아니면 구조, 시각, 기기 지표·게임 기록, 서버 결과를 차례로 점검하고, storage가 있으면 파일도 점검함
    4. 세션 등록이나 complete의 client_meta에 dev가 true면 개발용으로 표시함
    """
    bundle = load(db, session)
    if session.status is SessionStatus.open:
        findings = [Finding(CAUTION, "NOT_COMPLETED", "complete 전인 세션임. 구조와 파일 점검은 건너뜀"), *check_times(bundle)]
    else:
        findings = [*check_structure(bundle), *check_times(bundle), *check_records(bundle), *check_server(bundle)]
        if storage is not None:
            findings += check_files(bundle, storage, now)
    dev = (session.client_meta or {}).get("dev") is True or (session.complete_client_meta or {}).get("dev") is True
    return Report(
        session_id=str(session.id),
        user_id=str(session.user_id),
        purpose=session.purpose.value,
        status=session.status.value,
        started_at=session.started_at.astimezone(KST).isoformat(),
        device_model=session.device_model,
        os_version=session.os_version,
        app_version=session.app_version,
        input_device=session.input_device,
        dev=dev,
        recordings=dict(Counter(recording.kind.value for recording in bundle.recordings)),
        device_measurements=len(bundle.device),
        server_measurements=len(bundle.server),
        game_runs=dict(Counter(run.game for run in bundle.game_runs)),
        findings=findings,
    )


def select_sessions(
    db: Session, session_id: uuid.UUID | None, user_id: uuid.UUID | None, since: date | None
) -> list[SessionRow]:
    """
    점검할 세션을 고름. 읽기만 함
    입력: db (DB 세션), session_id (세션 ID, 선택), user_id (사용자 ID, 선택), since (이 날짜 한국 시간 0시 이후에 시작한 세션만, 선택)
    출력: 세션 행 목록(시작 시각·ID 순). 조건이 없으면 모든 세션
    알고리즘
    1. 주어진 조건을 모두 걸어 세션을 시작 시각·ID 순으로 읽음
    """
    query = select(SessionRow)
    if session_id is not None:
        query = query.where(SessionRow.id == session_id)
    if user_id is not None:
        query = query.where(SessionRow.user_id == user_id)
    if since is not None:
        query = query.where(SessionRow.started_at >= datetime.combine(since, time.min, tzinfo=KST))
    return list(db.scalars(query.order_by(SessionRow.started_at, SessionRow.id)))


def render(reports: list[Report], files_checked: bool) -> str:
    """
    점검 결과를 사람이 읽는 글로 만듦
    입력: reports (세션별 점검 결과), files_checked (녹음 파일도 점검했는지)
    출력: 여러 줄 문자열. 세션마다 정보 두 줄과 Finding(없으면 "문제 없음"), 맨 끝에 전체 요약
    알고리즘
    1. 세션마다 ID, 목적, 시작 시각(한국 시간, 분까지), 상태(개발용이면 표시)를 한 줄로 적음
    2. 기기·OS·앱 버전·입력 장치와 종류별 녹음 수, 기기 지표 수, 서버 지표 수, 게임별 기록 수를 한 줄로 적음
    3. Finding을 "[수준] 항목 이름: 설명 (녹음 ID)"로 한 줄씩 적음
    4. 끝에 세션 수, 위반 수(위반이 있는 세션 수), 주의 수, 파일 점검 여부를 적음
    """
    lines = []
    for report in reports:
        dev = " · 개발용" if report.dev else ""
        lines.append(
            f"세션 {report.session_id} · {report.purpose} · {report.started_at[:16].replace('T', ' ')} KST · {report.status}{dev}"
        )
        lines.append(
            f"  기기 {report.device_model}, OS {report.os_version}, 앱 {report.app_version}, 입력 {report.input_device} · "
            f"녹음 {report.recordings or '없음'} · 기기 지표 {report.device_measurements}개 · "
            f"서버 지표 {report.server_measurements}개 · 게임 기록 {report.game_runs or '없음'}"
        )
        for finding in report.findings:
            where = f" (녹음 {finding.recording_id})" if finding.recording_id else ""
            lines.append(f"  [{finding.level}] {finding.code}: {finding.message}{where}")
        if not report.findings:
            lines.append("  문제 없음")
    violations = sum(report.count(VIOLATION) for report in reports)
    with_violations = sum(report.count(VIOLATION) > 0 for report in reports)
    cautions = sum(report.count(CAUTION) for report in reports)
    files = "녹음 파일 점검함" if files_checked else "녹음 파일은 점검하지 않음(--no-s3)"
    lines.append(f"세션 {len(reports)}개 점검: 위반 {violations}건(세션 {with_violations}개), 주의 {cautions}건. {files}")
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    db: Session | None = None,
    storage: RecordingStorage | None = None,
    out: Callable[[str], None] = print,
    now: datetime | None = None,
) -> int:
    """
    앱이 보낸 세션을 점검하는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --session(세션 ID), --user(사용자 ID), --all(모든 세션) 중 하나는 꼭 줌. --since(YYYY-MM-DD, 그날 한국 시간 0시 이후 시작한 세션만), --no-s3(녹음 파일은 보지 않음), --json(JSON으로 출력)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - storage: 녹음 버킷 저장소(시험용 주입, 없으면 환경 변수·backend/.env의 AWS_REGION·AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·S3_BUCKET으로 만듦. --no-s3면 만들지 않음)
    - out: 결과를 출력할 함수(기본 print)
    - now: 지금 시각(시험용 주입, 없으면 현재 UTC 시각)
    출력: 종료 코드(0 = 위반 없음, 1 = 위반이 있음, 2 = 점검할 세션을 찾지 못함). 결과를 out으로, 입력 오류를 표준 오류로 씀. DB·S3는 읽기만 함
    알고리즘
    1. 명령줄 인자를 읽음
    2. db가 없으면 운영 접속 정보로 DB 세션을 만들고, --no-s3가 아니고 storage가 없으면 저장소를 만듦
    3. 조건에 맞는 세션을 고름(select_sessions). 하나도 없으면 2를 돌려줌
    4. 세션마다 점검함(inspect)
    5. --json이면 JSON으로, 아니면 render로 출력함
    6. 위반이 하나라도 있으면 1, 없으면 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="앱이 보낸 세션이 API 명세와 어긋나는 점을 찾는다")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--session", type=uuid.UUID, help="세션 ID")
    scope.add_argument("--user", type=uuid.UUID, help="사용자 ID")
    scope.add_argument("--all", action="store_true", help="모든 세션")
    parser.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD. 그날 한국 시간 0시 이후 시작한 세션만")
    parser.add_argument("--no-s3", action="store_true", help="녹음 파일은 보지 않고 DB에 있는 것만 점검한다")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    args = parser.parse_args(argv)

    if db is None:
        # 실제 실행에서만 운영 접속 정보를 읽는다.
        from app.db.engine import get_engine

        db = Session(get_engine())
    if args.no_s3:
        storage = None
    elif storage is None:
        from app.storage.s3 import get_storage

        storage = get_storage()

    sessions = select_sessions(db, args.session, args.user, args.since)
    if not sessions:
        print("점검할 세션을 찾지 못했습니다", file=sys.stderr)
        return 2
    now = now or datetime.now(UTC)
    reports = [inspect(db, storage, session, now) for session in sessions]
    if args.json:
        out(json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2))
    else:
        out(render(reports, storage is not None))
    return 1 if any(report.count(VIOLATION) for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
