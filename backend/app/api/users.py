from uuid import UUID

from fastapi import APIRouter
from pydantic import UUID4
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.deps import Db
from app.api.errors import INVALID_REQUEST, ApiError, ErrorCode, error_doc
from app.api.progress import metric_histories
from app.config.metrics import METRICS
from app.db.models import MeasurementRow, MeasurementSource, SessionRow, UserRow
from app.schemas.common import Metric, Purpose, SessionStatus, Unit
from app.schemas.history import DeviceSessionMeasurements, History
from app.schemas.sessions import DeviceMeasurement
from app.schemas.users import User, UserIn

router = APIRouter(tags=["users"])

# 진전도 응답에 넣는 기기 값 회차 수. 앱을 다시 설치했을 때 난이도 기준값을 복구하는 용도
RECENT_DEVICE_SESSIONS = 3
METRIC_ORDER = {metric: index for index, metric in enumerate(METRICS)}


def require_user(db: Session, user_id: UUID) -> UserRow:
    """
    사용자 ID로 사용자 행을 찾고, 없으면 404 오류를 냄
    입력: db (DB 세션), user_id (사용자 ID, UUID)
    출력: 사용자 행(UserRow). 없으면 ApiError(404, USER_NOT_FOUND)를 일으킴
    알고리즘
    1. DB에서 기본 키로 사용자 행을 읽음
    2. 없으면 404 USER_NOT_FOUND 오류를 일으키고, 있으면 그 행을 돌려줌
    """
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
    """\f
    PUT /v1/users/{user_id}: 사용자를 등록하고, 같은 ID로 다시 오면 처음 값과 비교함
    입력: user_id (앱이 만든 사용자 ID, UUID v4), body (요청 본문: created_at 앱에서 사용자를 만든 시각), db (요청마다 여는 DB 세션)
    출력: 등록된 사용자(id, created_at). DB users 표에 행을 넣고 커밋함. 같은 ID에 다른 created_at이 있으면 409 ID_CONFLICT
    알고리즘
    1. 같은 ID가 동시에 와도 한 번만 들어가도록, 이미 있으면 넣지 않는 INSERT(ON CONFLICT DO NOTHING)를 실행함
    2. 그 ID의 행을 다시 읽어 created_at이 요청 값과 다르면 409 ID_CONFLICT 오류를 일으킴
    3. 커밋하고 저장된 값을 돌려줌
    """
    # 같은 ID가 동시에 와도 한 번만 들어가도록, 이미 있으면 넣지 않고 있는 값과 비교한다.
    db.execute(
        insert(UserRow).values(id=user_id, created_at=body.created_at).on_conflict_do_nothing(index_elements=[UserRow.id])
    )
    user = db.get(UserRow, user_id)
    if user.created_at != body.created_at:
        raise ApiError(409, ErrorCode.id_conflict, "같은 user_id에 다른 created_at이 이미 있습니다")
    db.commit()
    return User(id=user.id, created_at=user.created_at)


def recent_device_measurements(db: Session, user_id: UUID) -> list[DeviceSessionMeasurements]:
    """
    complete를 받은 훈련 세션 중 기기 값이 있는 최근 회차들의 기기 지표를 모음
    입력: db (DB 세션), user_id (사용자 ID)
    출력: 회차별 기기 지표 목록 (최대 RECENT_DEVICE_SESSIONS = 3회차, 오래된 회차부터. 회차 안의 지표는 METRICS 순서)
    알고리즘
    1. 이 사용자의 훈련 세션 중 상태가 open이 아니고(complete를 받음) 기기 측정값이 하나라도 있는 세션을, 시작 시각·ID 내림차순으로 3개까지 고름
    2. 고른 세션들의 기기 측정값을 한 번에 읽어 세션별로 묶음
    3. 세션을 오래된 회차부터 놓고, 세션마다 지표를 METRICS 순서로 정렬해 돌려줌
    """
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
    """\f
    GET /v1/users/{user_id}/history: 사용자의 진전도와 변화 판정, 최근 기기 값을 돌려줌
    입력: user_id (사용자 ID, UUID v4), db (요청마다 여는 DB 세션)
    출력: 진전도 응답(user_id, 지표별 진전도 metrics, recent_device_measurements). 없는 사용자면 404 USER_NOT_FOUND
    알고리즘
    1. 사용자가 있는지 확인함(require_user)
    2. 지표별 기준선·회차 값·판정을 metric_histories로 만듦
    3. 최근 3회차 기기 값을 recent_device_measurements로 모아 함께 돌려줌
    """
    require_user(db, user_id)
    return History(
        user_id=user_id,
        metrics=metric_histories(db, user_id),
        recent_device_measurements=recent_device_measurements(db, user_id),
    )
