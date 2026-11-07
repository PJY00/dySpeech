from uuid import UUID

from fastapi import APIRouter
from pydantic import UUID4
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.deps import Db
from app.api.errors import INVALID_REQUEST, ApiError, ErrorCode, error_doc
from app.config.metrics import METRICS, PROGRESS_METRICS
from app.db.models import MeasurementRow, MeasurementSource, SessionRow, UserRow
from app.schemas.common import ChangeStatus, Metric, Purpose, SessionStatus, Unit
from app.schemas.history import DeviceSessionMeasurements, History, MetricHistory
from app.schemas.sessions import DeviceMeasurement
from app.schemas.users import User, UserIn

router = APIRouter(tags=["users"])

# 진전도 응답에 넣는 기기 값 회차 수. 앱을 다시 설치했을 때 난이도 기준값을 복구하는 용도
RECENT_DEVICE_SESSIONS = 3
METRIC_ORDER = {metric: index for index, metric in enumerate(METRICS)}


def require_user(db: Session, user_id: UUID) -> UserRow:
    user = db.get(UserRow, user_id)
    if user is None:
        raise ApiError(404, ErrorCode.user_not_found, "사용자가 없습니다. 사용자 등록부터 하세요")
    return user


@router.put(
    "/users/{user_id}",
    summary="사용자 등록",
    responses={409: error_doc("같은 user_id에 다른 내용이 이미 있음"), **INVALID_REQUEST},
)
def put_user(user_id: UUID4, body: UserIn, db: Db) -> User:
    # 같은 ID가 동시에 와도 한 번만 들어가도록, 이미 있으면 넣지 않고 있는 값과 비교한다.
    db.execute(
        insert(UserRow).values(id=user_id, created_at=body.created_at).on_conflict_do_nothing(index_elements=[UserRow.id])
    )
    user = db.get(UserRow, user_id)
    if user.created_at != body.created_at:
        raise ApiError(409, ErrorCode.id_conflict, "같은 user_id에 다른 created_at이 이미 있습니다")
    db.commit()
    return User(id=user.id, created_at=user.created_at)


def no_data(metric: Metric) -> MetricHistory:
    spec = METRICS[metric]
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


def recent_device_measurements(db: Session, user_id: UUID) -> list[DeviceSessionMeasurements]:
    """complete를 받은 훈련 세션 중 기기 값이 있는 최근 회차들. 오래된 회차부터"""
    has_device_values = exists().where(
        MeasurementRow.session_id == SessionRow.id, MeasurementRow.source == MeasurementSource.device
    )
    sessions = db.scalars(
        select(SessionRow)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.purpose == Purpose.training,
            SessionRow.status != SessionStatus.open,
            has_device_values,
        )
        .order_by(SessionRow.started_at.desc(), SessionRow.id.desc())
        .limit(RECENT_DEVICE_SESSIONS)
    ).all()
    rows = db.scalars(
        select(MeasurementRow).where(
            MeasurementRow.session_id.in_([s.id for s in sessions]), MeasurementRow.source == MeasurementSource.device
        )
    ).all()
    by_session: dict[UUID, list[MeasurementRow]] = {}
    for row in rows:
        by_session.setdefault(row.session_id, []).append(row)
    return [
        DeviceSessionMeasurements(
            session_id=session.id,
            started_at=session.started_at,
            measurements=[
                DeviceMeasurement(
                    metric=Metric(row.metric), value=row.value, unit=Unit(row.unit), low_reliability=row.low_reliability
                )
                for row in sorted(by_session[session.id], key=lambda r: METRIC_ORDER[Metric(r.metric)])
            ],
        )
        for session in reversed(sessions)
    ]


@router.get(
    "/users/{user_id}/history",
    summary="진전도와 변화 판정",
    responses={404: error_doc("사용자 없음"), **INVALID_REQUEST},
)
def get_history(user_id: UUID4, db: Db) -> History:
    require_user(db, user_id)
    # 서버 지표(공식 값)는 3단계부터 계산한다. 그 전에는 서버 값이 없으므로 모든 진전도 지표가 no_data다.
    return History(
        user_id=user_id,
        metrics=[no_data(metric) for metric in PROGRESS_METRICS],
        recent_device_measurements=recent_device_measurements(db, user_id),
    )
