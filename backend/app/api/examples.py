"""임시 서버의 예시 응답.

저장하지 않는다. 요청에 있는 ID와 값은 그대로 넣어 돌려주고, 나머지는 형식을 보여주기 위한 고정 값이다.
URL은 예시용 도메인(example.com)이라 실제로 올리거나 받을 수 없다.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.config.metrics import METRICS, PROGRESS_METRICS
from app.schemas.common import ChangeStatus, Mdc, MdcScale, Metric, Purpose, SessionStatus
from app.schemas.history import (
    BaselinePoint,
    DeviceSessionMeasurements,
    History,
    LatestPoint,
    MetricHistory,
    SeriesPoint,
)
from app.schemas.recordings import RecordingUrl, Upload, UploadUrlsIn, UploadUrlsOut
from app.schemas.sessions import (
    CompleteAccepted,
    CompleteIn,
    DeviceMeasurement,
    ServerMeasurement,
    Session,
    SessionDetail,
    SessionIn,
)
from app.schemas.users import User, UserIn

EXAMPLE_ALGORITHM_VERSION = "0.0.0-example"
UPLOAD_URL_TTL = timedelta(minutes=15)
RECORDING_URL_TTL = timedelta(minutes=10)
EXAMPLE_URL_BASE = "https://s3.example.com/example"

EXAMPLE_USER_ID = UUID("3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40")
FIRST_SESSION_ID = UUID("8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58")
SECOND_SESSION_ID = UUID("5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12")
FIRST_STARTED_AT = datetime(2026, 11, 20, 9, 15, tzinfo=UTC)
SECOND_STARTED_AT = datetime(2026, 11, 27, 9, 15, tzinfo=UTC)

# 한 회차의 서버 값 예시
SESSION_VALUES = {
    Metric.max_phonation_time: 12.4,
    Metric.loudness_decay_slope: -1.8,
    Metric.mean_level_change: 0.0,
    Metric.articulation_rate: 4.1,
    Metric.speech_rate: 3.5,
    Metric.pause_ratio: 14.6,
    Metric.f0_sd_semitones: 2.3,
    Metric.pitch_range_semitones: 7.8,
    Metric.sustained_level: -24.5,
    Metric.f0_median: 182.0,
    Metric.noise_floor: -62.0,
}

# 진전도 예시: (기준선 값, 최근 값, 최근 값이 신뢰 낮음인지, 최소 측정 가능 변화량, 판정). None은 값 없음
HISTORY_VALUES = {
    Metric.max_phonation_time: (12.4, 14.9, False, Mdc(value=2.0, scale=MdcScale.raw), ChangeStatus.increase),
    Metric.loudness_decay_slope: (-1.8, -1.5, False, Mdc(value=0.9, scale=MdcScale.raw), ChangeStatus.no_change),
    Metric.mean_level_change: (0.0, 1.2, False, None, ChangeStatus.pending_mdc),
    Metric.articulation_rate: (4.1, 4.3, False, Mdc(value=12.0, scale=MdcScale.log), ChangeStatus.no_change),
    Metric.speech_rate: (3.5, 3.8, False, None, ChangeStatus.pending_mdc),
    Metric.pause_ratio: (14.6, 11.0, True, Mdc(value=5.0, scale=MdcScale.raw), ChangeStatus.low_reliability),
    Metric.f0_sd_semitones: (2.3, 1.9, False, Mdc(value=0.3, scale=MdcScale.raw), ChangeStatus.decrease),
    Metric.pitch_range_semitones: None,
}

# 최근 회차의 기기 값 예시(오래된 회차부터)
DEVICE_VALUES = [
    (FIRST_SESSION_ID, FIRST_STARTED_AT, {Metric.max_phonation_time: 11.8, Metric.f0_median: 178.0, Metric.noise_floor: -60.5}),
    (SECOND_SESSION_ID, SECOND_STARTED_AT, {Metric.max_phonation_time: 14.2, Metric.f0_median: 181.5, Metric.noise_floor: -61.0}),
]


def _now() -> datetime:
    return datetime.now(UTC)


def user(user_id: UUID, body: UserIn) -> User:
    return User(id=user_id, created_at=body.created_at)


def session(session_id: UUID, body: SessionIn) -> Session:
    return Session(
        id=session_id,
        ended_at=None,
        status=SessionStatus.open,
        is_baseline=False,
        **body.model_dump(exclude={"client_meta"}),
    )


def upload_urls(session_id: UUID, body: UploadUrlsIn) -> UploadUrlsOut:
    expires_at = _now() + UPLOAD_URL_TTL
    return UploadUrlsOut(
        uploads=[
            Upload(
                recording_id=recording.recording_id,
                url=f"{EXAMPLE_URL_BASE}/{session_id}/{recording.recording_id}.wav?X-Amz-Signature=example",
                method="PUT",
                headers={"Content-Type": "audio/wav"},
                expires_at=expires_at,
            )
            for recording in body.recordings
        ]
    )


def complete(session_id: UUID, body: CompleteIn) -> CompleteAccepted:
    return CompleteAccepted(session_id=session_id, status=SessionStatus.processing)


def session_detail(session_id: UUID) -> SessionDetail:
    return SessionDetail(
        id=session_id,
        user_id=EXAMPLE_USER_ID,
        purpose=Purpose.training,
        started_at=FIRST_STARTED_AT,
        ended_at=FIRST_STARTED_AT + timedelta(minutes=26),
        device_model="SM-S921N",
        os_version="Android 16",
        app_version="0.1.0",
        input_device="wired_headset",
        sample_rate=48000,
        status=SessionStatus.done,
        is_baseline=True,
        server_measurements=[
            ServerMeasurement(
                metric=metric,
                value=value,
                unit=METRICS[metric].unit,
                low_reliability=False,
                algorithm_version=EXAMPLE_ALGORITHM_VERSION,
            )
            for metric, value in SESSION_VALUES.items()
        ],
        error=None,
    )


def _change(baseline: float, latest: float, mdc: Mdc | None) -> float:
    if mdc is not None and mdc.scale is MdcScale.log:
        return round((latest / baseline - 1) * 100, 1)
    return round(latest - baseline, 2)


def _metric_history(metric: Metric) -> MetricHistory:
    spec = METRICS[metric]
    values = HISTORY_VALUES[metric]
    if values is None:
        return MetricHistory(
            metric=metric,
            unit=spec.unit,
            desired_direction=spec.desired_direction,
            baseline=None,
            series=[],
            latest=None,
            mdc=None,
            status=ChangeStatus.no_data,
        )
    baseline, latest, latest_low_reliability, mdc, status = values
    return MetricHistory(
        metric=metric,
        unit=spec.unit,
        desired_direction=spec.desired_direction,
        baseline=BaselinePoint(session_id=FIRST_SESSION_ID, value=baseline),
        series=[
            SeriesPoint(session_id=FIRST_SESSION_ID, started_at=FIRST_STARTED_AT, value=baseline, low_reliability=False),
            SeriesPoint(
                session_id=SECOND_SESSION_ID,
                started_at=SECOND_STARTED_AT,
                value=latest,
                low_reliability=latest_low_reliability,
            ),
        ],
        latest=LatestPoint(value=latest, change=_change(baseline, latest, mdc)),
        mdc=mdc,
        status=status,
    )


def history(user_id: UUID) -> History:
    return History(
        user_id=user_id,
        metrics=[_metric_history(metric) for metric in PROGRESS_METRICS],
        recent_device_measurements=[
            DeviceSessionMeasurements(
                session_id=session_id,
                started_at=started_at,
                measurements=[
                    DeviceMeasurement(metric=metric, value=value, unit=METRICS[metric].unit)
                    for metric, value in values.items()
                ],
            )
            for session_id, started_at, values in DEVICE_VALUES
        ],
    )


def recording_url(recording_id: UUID) -> RecordingUrl:
    return RecordingUrl(
        recording_id=recording_id,
        url=f"{EXAMPLE_URL_BASE}/{recording_id}.wav?X-Amz-Signature=example",
        expires_at=_now() + RECORDING_URL_TTL,
    )
