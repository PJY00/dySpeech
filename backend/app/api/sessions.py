import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import UUID4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Db, Storage
from app.api.errors import (
    INVALID_REQUEST,
    ApiError,
    ErrorCode,
    InvalidRecordingsError,
    MissingRecordingsError,
    RequestRuleError,
    error_doc,
)
from app.api.users import METRIC_ORDER, require_user
from app.db.models import GameRunRow, MeasurementRow, MeasurementSource, RecordingRow, SessionRow
from app.schemas.common import ErrorDetail, FieldError, Metric, RecordingKind, SessionStatus, Unit, ValidationErrorResponse
from app.schemas.recordings import RecordingIn, Upload, UploadUrlsIn, UploadUrlsOut
from app.schemas.sessions import (
    CompleteAccepted,
    CompleteIn,
    InvalidRecording,
    InvalidRecordingsResponse,
    MissingRecordingsResponse,
    ServerMeasurement,
    Session,
    SessionDetail,
    SessionIn,
)
from app.storage.s3 import RECORDING_RETENTION, recording_key
from app.storage.wav import check_uploaded_wav

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])

SESSION_NOT_FOUND = {404: error_doc("세션 없음")}

# 녹음이 무엇인지 정하는 필드. 같은 recording_id면 언제나 같아야 한다.
RECORDING_IDENTITY = ("kind", "trial_no", "sentence_id", "game_run_id", "agc_signal")
# 파일에 대한 필드. complete 전에는 바뀌어도 된다(규격에 맞게 다시 만든 파일을 같은 ID로 올릴 때).
RECORDING_FILE = ("duration_s", "sample_rate", "size_bytes")


def find_session(db: DbSession, session_id: UUID, lock: bool = False) -> SessionRow:
    # 업로드 URL 발급과 complete는 세션 행을 잠가 차례로 처리한다. 녹음 목록이 확인 도중 바뀌지 않게 하기 위해서다.
    session = db.get(SessionRow, session_id, with_for_update=lock)
    if session is None:
        raise ApiError(404, ErrorCode.session_not_found, "세션이 없습니다. 세션 등록부터 하세요")
    return session


def session_out(row: SessionRow) -> Session:
    return Session.model_validate(row, from_attributes=True)


@router.put(
    "/sessions/{session_id}",
    summary="세션 등록",
    responses={
        404: error_doc("user_id의 사용자 없음"),
        409: error_doc("같은 session_id에 다른 내용이 이미 있음"),
        **INVALID_REQUEST,
    },
)
def put_session(session_id: UUID4, body: SessionIn, db: Db) -> Session:
    require_user(db, body.user_id)
    values = body.model_dump(exclude={"client_meta"})
    db.execute(
        insert(SessionRow)
        .values(id=session_id, client_meta=body.client_meta, **values)
        .on_conflict_do_nothing(index_elements=[SessionRow.id])
    )
    session = db.get(SessionRow, session_id)
    # client_meta는 서버가 해석하지 않는 부가 정보라 비교하지 않는다. 처음 받은 값을 둔다.
    different = [name for name, value in values.items() if getattr(session, name) != value]
    if different:
        raise ApiError(409, ErrorCode.id_conflict, f"같은 session_id에 다른 내용이 이미 있습니다: {', '.join(different)}")
    db.commit()
    return session_out(session)


def recording_values(recording: RecordingIn) -> dict:
    values = recording.model_dump(include=set(RECORDING_IDENTITY + RECORDING_FILE))
    for name in ("sentence_id", "agc_signal"):
        if values[name] is not None:
            values[name] = str(values[name])
    return values


@router.post(
    "/sessions/{session_id}/upload-urls",
    summary="녹음 업로드 URL 발급",
    responses={
        **SESSION_NOT_FOUND,
        409: error_doc("같은 recording_id에 다른 내용이 이미 있음"),
        **INVALID_REQUEST,
    },
)
def post_upload_urls(session_id: UUID4, body: UploadUrlsIn, db: Db, storage: Storage) -> UploadUrlsOut:
    session = find_session(db, session_id, lock=True)
    ids = [recording.recording_id for recording in body.recordings]
    existing = {row.id: row for row in db.scalars(select(RecordingRow).where(RecordingRow.id.in_(ids)))}
    is_open = session.status is SessionStatus.open
    keys: dict[UUID, str] = {}
    conflicts: list[UUID] = []
    added = 0
    for recording in body.recordings:
        values = recording_values(recording)
        row = existing.get(recording.recording_id)
        if row is None:
            key = recording_key(session.user_id, session_id, recording.recording_id)
            db.add(RecordingRow(id=recording.recording_id, session_id=session_id, s3_key=key, **values))
            keys[recording.recording_id] = key
            added += 1
            continue
        same_identity = row.session_id == session_id and all(getattr(row, n) == values[n] for n in RECORDING_IDENTITY)
        same_file = all(getattr(row, n) == values[n] for n in RECORDING_FILE)
        if not same_identity or not (same_file or is_open):
            conflicts.append(recording.recording_id)
            continue
        for name in RECORDING_FILE:
            setattr(row, name, values[name])
        keys[recording.recording_id] = row.s3_key
    if conflicts:
        raise ApiError(
            409, ErrorCode.id_conflict, f"같은 recording_id에 다른 내용이 이미 있습니다: {', '.join(map(str, conflicts))}"
        )
    if added and not is_open:
        # complete 뒤에 새로 등록한 녹음은 이 세션 처리에 들어가지 않는다.
        logger.warning("complete를 받은 세션(상태 %s)에 새 녹음 %d개가 등록됨", session.status, added)
    try:
        db.commit()
    except IntegrityError as error:
        # 다른 세션이 같은 recording_id를 동시에 등록한 경우
        db.rollback()
        raise ApiError(409, ErrorCode.id_conflict, "같은 recording_id가 다른 세션에 이미 있습니다") from error

    now = datetime.now(UTC)
    uploads = []
    for recording_id in ids:
        url = storage.upload_url(keys[recording_id], now)
        uploads.append(
            Upload(recording_id=recording_id, url=url.url, method=url.method, headers=url.headers, expires_at=url.expires_at)
        )
    return UploadUrlsOut(uploads=uploads)


def check_game_runs(db: DbSession, body: CompleteIn, recordings: list[RecordingRow]) -> None:
    """게임 녹음의 게임 라운드가 요청에 있는지, 게임 라운드 ID가 다른 세션에서 쓰이지 않았는지 본다."""
    sent = {run.game_run_id for run in body.game_runs}
    absent = sorted(
        {str(r.game_run_id) for r in recordings if r.kind is RecordingKind.game and r.game_run_id not in sent}
    )
    if absent:
        reason = f"game 녹음의 game_run_id가 game_runs에 없습니다: {', '.join(absent)}"
        raise RequestRuleError([FieldError(path="body.game_runs", reason=reason)])
    taken = set(db.scalars(select(GameRunRow.id).where(GameRunRow.id.in_(list(sent)))))
    if taken:
        raise RequestRuleError(
            [
                FieldError(path=f"body.game_runs[{index}].game_run_id", reason="다른 세션에 이미 있는 game_run_id입니다")
                for index, run in enumerate(body.game_runs)
                if run.game_run_id in taken
            ]
        )


@router.post(
    "/sessions/{session_id}/complete",
    status_code=202,
    summary="세션 완료",
    responses={
        **SESSION_NOT_FOUND,
        409: error_doc(
            "올라오지 않은 녹음이 있음. missing_recording_ids의 파일만 다시 올리고 재요청",
            MissingRecordingsResponse,
        ),
        422: error_doc(
            "요청이 API 명세와 다름(VALIDATION_ERROR, fields에 문제 필드의 경로와 이유), "
            "또는 올린 WAV가 규격과 다름(INVALID_RECORDING_FORMAT, invalid_recordings에 녹음 ID와 이유)",
            ValidationErrorResponse | InvalidRecordingsResponse,
        ),
    },
)
def post_complete(session_id: UUID4, body: CompleteIn, db: Db, storage: Storage, request: Request) -> CompleteAccepted:
    session = find_session(db, session_id, lock=True)
    if session.status is not SessionStatus.open:
        # 이미 받은 complete다. 다시 저장하지 않고 지금 상태를 돌려준다.
        return CompleteAccepted(session_id=session_id, status=session.status)

    recordings = db.scalars(
        select(RecordingRow).where(RecordingRow.session_id == session_id).order_by(RecordingRow.created_at, RecordingRow.id)
    ).all()
    check_game_runs(db, body, recordings)

    # 검사 순서: 올라오지 않은 녹음(409) → 파일 규격(422). 어느 쪽이든 세션은 open으로 남아 다시 보낼 수 있다.
    objects = {r.id: storage.info(r.s3_key) for r in recordings}
    missing = [r.id for r in recordings if objects[r.id] is None]
    if missing:
        # 앱 연동 때 원인을 찾을 수 있도록 개수와 이유를 남긴다(ID는 응답에 있다).
        logger.info("complete 거부(409): 올라오지 않은 녹음 %d개 / 전체 %d개", len(missing), len(recordings))
        raise MissingRecordingsError(missing)
    invalid = [
        InvalidRecording(recording_id=r.id, reason=reason)
        for r in recordings
        if (reason := check_uploaded_wav(storage, r.s3_key, r.sample_rate))
    ]
    if invalid:
        reasons = sorted({item.reason for item in invalid})
        logger.info("complete 거부(422): 규격과 다른 녹음 %d개. %s", len(invalid), " | ".join(reasons))
        raise InvalidRecordingsError(invalid)

    now = datetime.now(UTC)
    for recording in recordings:
        recording.uploaded_at = now
        recording.retain_until = objects[recording.id].last_modified + RECORDING_RETENTION
    db.add_all(
        MeasurementRow(
            session_id=session_id,
            metric=m.metric.value,
            value=m.value,
            unit=m.unit.value,
            source=MeasurementSource.device,
            low_reliability=m.low_reliability,
        )
        for m in body.device_measurements
    )
    db.add_all(
        GameRunRow(id=run.game_run_id, session_id=session_id, **run.model_dump(exclude={"game_run_id"}))
        for run in body.game_runs
    )
    session.ended_at = body.ended_at
    session.complete_client_meta = body.client_meta
    # 서버 지표 계산(3단계)이 끝나면 done 또는 failed가 된다.
    session.status = SessionStatus.processing
    try:
        db.commit()
    except IntegrityError as error:
        # 다른 세션이 같은 game_run_id를 동시에 저장한 경우
        db.rollback()
        raise RequestRuleError(
            [FieldError(path="body.game_runs", reason="다른 세션에 이미 있는 game_run_id가 있습니다")]
        ) from error
    # 처리 작업이 기다리지 않고 바로 계산을 시작하게 깨운다.
    worker = getattr(request.app.state, "processing_worker", None)
    if worker is not None:
        worker.wake()
    return CompleteAccepted(session_id=session_id, status=session.status)


@router.get(
    "/sessions/{session_id}",
    summary="처리 상태와 서버 지표",
    responses={**SESSION_NOT_FOUND, **INVALID_REQUEST},
)
def get_session(session_id: UUID4, db: Db) -> SessionDetail:
    session = find_session(db, session_id)
    rows = db.scalars(
        select(MeasurementRow).where(
            MeasurementRow.session_id == session_id, MeasurementRow.source == MeasurementSource.server
        )
    ).all()
    measurements = [
        ServerMeasurement(
            metric=Metric(row.metric),
            value=row.value,
            unit=Unit(row.unit),
            low_reliability=row.low_reliability,
            algorithm_version=row.algorithm_version,
        )
        for row in sorted(rows, key=lambda r: (METRIC_ORDER[Metric(r.metric)], r.algorithm_version or ""))
    ]
    return SessionDetail(
        **session_out(session).model_dump(),
        server_measurements=measurements,
        error=ErrorDetail.model_validate(session.error) if session.error else None,
    )
