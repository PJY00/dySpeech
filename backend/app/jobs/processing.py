"""처리 대기(processing) 세션의 서버 지표를 계산해 저장하고 done 또는 failed로 바꾼다.

- 세션은 시작 시각이 이른 것부터 하나씩 꺼낸다. 계산 전에 시도 횟수를 먼저 1 늘려 저장한다.
  메모리 부족 등으로 서버가 죽었다 다시 뜨면 같은 세션을 다시 잡는데, 시작이 2번을 넘으면 failed로 바꿔
  같은 세션 때문에 서버가 계속 죽는 것을 막는다.
- failed는 파일을 읽지 못하거나 계산 중 예외가 났을 때만이다. 일부 지표만 못 구하면 done이고 그 지표만 없다.
- 같은 알고리즘 버전으로 다시 계산하면 그 버전의 서버 값을 지우고 새로 저장한다.
- 배포 때 새 서버와 이전 서버가 잠깐 함께 돌면 같은 세션을 두 작업이 계산할 수 있다.
  그래서 계산하는 동안에는 DB에 아무것도 쓰지 않고, 쓰기 직전에 세션 행을 잠근 뒤 아직 processing일 때만 쓴다.
  이미 done·failed면 아무것도 쓰지 않는다. 잠그는 순서는 항상 세션 → 사용자다(교착을 막는다).
"""

import logging
import time
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config.metrics import METRICS
from app.db.models import MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Metric, Purpose, RecordingKind, SessionStatus
from app.storage.s3 import RecordingStorage

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
FAILED = {"code": "PROCESSING_FAILED", "message": "서버가 녹음에서 지표를 계산하지 못했습니다"}
ABANDONED = {"code": "PROCESSING_ABANDONED", "message": f"서버 지표 계산을 {MAX_ATTEMPTS}번 시작했지만 끝내지 못했습니다"}


def claim_next(db: Session) -> UUID | None:
    """처리할 세션 하나를 고르고 시도 횟수를 1 늘려 저장한다. 없으면 None"""
    while True:
        session = db.scalars(
            select(SessionRow)
            .where(SessionRow.status == SessionStatus.processing)
            .order_by(SessionRow.started_at, SessionRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if session is None:
            db.rollback()
            return None
        if session.processing_attempts >= MAX_ATTEMPTS:
            session.status = SessionStatus.failed
            session.error = ABANDONED
            db.commit()
            logger.warning("지표 계산 포기: 시작 %d번에도 끝나지 않음. 세션 %s", session.processing_attempts, session.id)
            continue
        session.processing_attempts += 1
        db.commit()
        return session.id


def uploaded_recordings(db: Session, session_id: UUID) -> list[RecordingRow]:
    return list(
        db.scalars(
            select(RecordingRow)
            .where(RecordingRow.session_id == session_id, RecordingRow.uploaded_at.is_not(None))
            .order_by(RecordingRow.trial_no.nulls_last(), RecordingRow.created_at, RecordingRow.id)
        )
    )


def stored_calibration(user: UserRow) -> tuple[float, float] | None:
    """같은 알고리즘 버전으로 구한 캘리브레이션만 쓴다."""
    value = user.calibration
    if value and value.get("algorithm_version") == ALGORITHM_VERSION:
        return value["f0_low_hz"], value["f0_high_hz"]
    return None


def save_calibration(user: UserRow, calibration: tuple[float, float], session_id: UUID) -> None:
    user.calibration = {
        "f0_low_hz": round(calibration[0], 2),
        "f0_high_hz": round(calibration[1], 2),
        "algorithm_version": ALGORITHM_VERSION,
        "session_id": str(session_id),
    }


def calibration_for(
    db: Session, storage: RecordingStorage, user: UserRow, session: SessionRow
) -> tuple[tuple[float, float] | None, UUID | None]:
    """(쓸 캘리브레이션, 다시 구했으면 그 녹음의 세션 ID). 저장된 값이 없거나 버전이 다르면,
    이 세션보다 앞선 가장 최근 캘리브레이션 녹음에서 다시 구한다. 저장은 결과를 쓸 때 한다."""
    stored = stored_calibration(user)
    if stored is not None:
        return stored, None
    glide = db.scalars(
        select(RecordingRow)
        .join(SessionRow)
        .where(
            SessionRow.user_id == user.id,
            SessionRow.started_at <= session.started_at,
            RecordingRow.kind == RecordingKind.calibration_glide,
            RecordingRow.uploaded_at.is_not(None),
        )
        .order_by(SessionRow.started_at.desc())
        .limit(1)
    ).first()
    if glide is None:
        return None, None
    from app.metrics.audio import load_wav
    from app.metrics.pitch import calibration_range

    calibration = calibration_range(load_wav(storage.read(glide.s3_key)).samples)
    return calibration, (glide.session_id if calibration is not None else None)


def reference_level(db: Session, session: SessionRow) -> tuple[UUID, float] | None:
    """평균 음량 변화의 기준: 같은 사용자·같은 목적에서 이 세션보다 먼저 시작해 평가 문장 음량이 있는 가장 이른 완료 회차"""
    row = db.execute(
        select(SessionRow.id, MeasurementRow.details)
        .join(MeasurementRow, MeasurementRow.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == session.user_id,
            SessionRow.purpose == session.purpose,
            SessionRow.status == SessionStatus.done,
            SessionRow.started_at < session.started_at,
            MeasurementRow.metric == Metric.mean_level_change.value,
            MeasurementRow.source == MeasurementSource.server,
            MeasurementRow.algorithm_version == ALGORITHM_VERSION,
        )
        .order_by(SessionRow.started_at, SessionRow.id)
        .limit(1)
    ).first()
    return None if row is None else (row.id, row.details["sentence_level_dbfs"])


def calculate(db: Session, storage: RecordingStorage, session: SessionRow):
    """읽기만 한다. (계산 결과, 다시 구한 캘리브레이션과 그 세션 ID 또는 None)"""
    from app.metrics.session import RecordingInput, compute_session

    user = db.get(UserRow, session.user_id)
    recordings = uploaded_recordings(db, session.id)
    has_glide = any(r.kind is RecordingKind.calibration_glide for r in recordings)
    calibration, source = (None, None) if has_glide else calibration_for(db, storage, user, session)
    inputs = [
        RecordingInput(r.kind, (lambda key=r.s3_key: storage.read(key)), r.sentence_id, r.trial_no) for r in recordings
    ]
    recomputed = (calibration, source) if source is not None else None
    return compute_session(inputs, calibration), recomputed


def lock_if_processing(db: Session, session_id: UUID) -> SessionRow | None:
    """세션 행을 잠그고 다시 읽는다. 아직 processing이면 그 행, 아니면(다른 작업이 먼저 끝냄) 되돌리고 None"""
    session = db.get(SessionRow, session_id, with_for_update=True, populate_existing=True)
    if session is not None and session.status is SessionStatus.processing:
        return session
    db.rollback()
    logger.info("다른 작업이 먼저 끝낸 세션이라 쓰지 않음: 세션 %s", session_id)
    return None


def save(db: Session, session: SessionRow, result, recomputed) -> int:
    """잠근 세션에 결과를 쓴다. 사용자 행(캘리브레이션)은 세션 다음에 잠근다."""
    user = db.get(UserRow, session.user_id, with_for_update=True, populate_existing=True)
    if recomputed is not None and stored_calibration(user) is None:
        save_calibration(user, *recomputed)
    db.execute(
        delete(MeasurementRow).where(
            MeasurementRow.session_id == session.id,
            MeasurementRow.source == MeasurementSource.server,
            MeasurementRow.algorithm_version == ALGORITHM_VERSION,
        )
    )
    values = list(result.values)
    if result.sentence_level is not None and session.purpose is not Purpose.agc_test:
        values.append(mean_level_change(db, session, result))
    db.add_all(
        MeasurementRow(
            session_id=session.id,
            metric=value.metric.value,
            value=value.value,
            unit=METRICS[value.metric].unit.value,
            source=MeasurementSource.server,
            algorithm_version=ALGORITHM_VERSION,
            low_reliability=value.low_reliability,
            details=value.details or None,
        )
        for value in values
    )
    if result.calibration is not None:
        stored = user.calibration or {}
        stored_session = db.get(SessionRow, UUID(stored["session_id"])) if stored.get("session_id") else None
        newer = stored_session is None or stored_session.started_at <= session.started_at
        if stored_calibration(user) is None or newer:
            save_calibration(user, result.calibration, session.id)
    if session.purpose is Purpose.training:
        first = db.scalar(select(SessionRow.id).where(SessionRow.user_id == session.user_id, SessionRow.is_baseline))
        if first is None:
            # 기준선: 사용자의 훈련 세션 중 처음 done이 된 회차
            session.is_baseline = True
    session.status = SessionStatus.done
    session.error = None
    return len(values)


def mean_level_change(db: Session, session: SessionRow, result):
    from app.metrics.session import MetricValue

    level = result.sentence_level
    low = any(v.low_reliability for v in result.values if v.metric is Metric.articulation_rate)
    reference = reference_level(db, session)
    if reference is None:
        # 이 회차가 기준이다.
        return MetricValue(Metric.mean_level_change, 0.0, low, {"sentence_level_dbfs": round(level, 3)})
    reference_id, reference_db = reference
    return MetricValue(
        Metric.mean_level_change,
        level - reference_db,
        low,
        {
            "sentence_level_dbfs": round(level, 3),
            "reference_session_id": str(reference_id),
            "reference_sentence_level_dbfs": reference_db,
        },
    )


def mark_failed(db: Session, session_id: UUID) -> SessionStatus | None:
    db.rollback()
    session = lock_if_processing(db, session_id)
    if session is None:
        return None
    session.status = SessionStatus.failed
    session.error = FAILED
    db.commit()
    return session.status


def process_session(db: Session, storage: RecordingStorage, session_id: UUID) -> SessionStatus | None:
    """계산해서 쓴다. 다른 작업이 먼저 끝낸 세션이면 아무것도 쓰지 않고 None"""
    session = db.get(SessionRow, session_id)
    if session is None or session.status is not SessionStatus.processing:
        return None
    started = time.perf_counter()
    try:
        result, recomputed = calculate(db, storage, session)
        session = lock_if_processing(db, session_id)
        if session is None:
            return None
        count = save(db, session, result, recomputed)
        db.commit()
    except Exception:
        logger.exception("지표 계산 실패. 세션 %s", session_id)
        return mark_failed(db, session_id)
    logger.info(
        "지표 계산 완료: 세션 %s, 지표 %d개, %.1f초%s",
        session_id,
        count,
        time.perf_counter() - started,
        f". 참고: {' / '.join(result.notes)}" if result.notes else "",
    )
    return session.status
