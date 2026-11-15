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
    """
    처리할 세션 하나를 고르고 시도 횟수를 1 늘려 저장함
    입력: db (DB 세션)
    출력: 고른 세션 ID. 처리할 세션이 없으면 None. DB에 시도 횟수(또는 포기 상태)를 쓰고 커밋함
    알고리즘
    1. processing 상태 세션 중 시작 시각·ID가 가장 이른 하나를, 다른 작업이 잠근 행은 건너뛰며(FOR UPDATE SKIP LOCKED) 잠가 읽음
    2. 없으면 되돌리고 None을 돌려줌
    3. 이미 MAX_ATTEMPTS(2번) 시작한 세션이면 failed와 PROCESSING_ABANDONED 오류로 바꿔 커밋하고, 경고 기록을 남긴 뒤 다음 세션을 찾음
    4. 아니면 시도 횟수를 1 늘려 커밋하고 그 세션 ID를 돌려줌
    """
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
    """
    세션에서 업로드가 확인된 녹음을 차례대로 읽음
    입력: db (DB 세션), session_id (세션 ID)
    출력: uploaded_at이 있는 녹음 행 목록. trial_no 순(없는 것은 뒤), 그다음 만든 시각·ID 순
    알고리즘
    1. 그 세션의 녹음 중 uploaded_at이 있는 행을 위 순서로 읽어 돌려줌
    """
    return list(
        db.scalars(
            select(RecordingRow)
            .where(RecordingRow.session_id == session_id, RecordingRow.uploaded_at.is_not(None))
            .order_by(RecordingRow.trial_no.nulls_last(), RecordingRow.created_at, RecordingRow.id)
        )
    )


def stored_calibration(user: UserRow) -> tuple[float, float] | None:
    """
    사용자 행에 저장된 캘리브레이션을 같은 알고리즘 버전일 때만 꺼냄
    입력: user (사용자 행. calibration JSON 열)
    출력: 편한 음높이 범위 (f0_low_hz, f0_high_hz), Hz. 없거나 알고리즘 버전이 다르면 None
    알고리즘
    1. 저장 값이 있고 algorithm_version이 지금 ALGORITHM_VERSION과 같으면 두 값을 돌려줌. 같은 알고리즘 버전으로 구한 캘리브레이션만 씀
    """
    value = user.calibration
    if value and value.get("algorithm_version") == ALGORITHM_VERSION:
        return value["f0_low_hz"], value["f0_high_hz"]
    return None


def save_calibration(user: UserRow, calibration: tuple[float, float], session_id: UUID) -> None:
    """
    편한 음높이 범위를 사용자 행에 기록함
    입력: user (사용자 행), calibration (편한 음높이 범위 (최저, 최고), Hz), session_id (그 값을 구한 녹음의 세션 ID)
    출력: 없음 (user.calibration에 f0_low_hz·f0_high_hz(Hz, 소수 둘째 자리), algorithm_version, session_id를 넣음. 커밋은 부르는 쪽이 함)
    알고리즘
    1. 두 값을 반올림하고 지금 알고리즘 버전과 세션 ID를 붙여 user.calibration에 넣음
    """
    user.calibration = {
        "f0_low_hz": round(calibration[0], 2),
        "f0_high_hz": round(calibration[1], 2),
        "algorithm_version": ALGORITHM_VERSION,
        "session_id": str(session_id),
    }


def calibration_for(
    db: Session, storage: RecordingStorage, user: UserRow, session: SessionRow
) -> tuple[tuple[float, float] | None, UUID | None]:
    """
    캘리브레이션 녹음이 없는 세션에 쓸 캘리브레이션을 구함
    입력: db (DB 세션), storage (녹음 저장소, S3), user (사용자 행), session (계산할 세션 행)
    출력: (쓸 편한 음높이 범위 (최저, 최고) Hz 또는 None, 녹음에서 다시 구했으면 그 녹음의 세션 ID 또는 None). 읽기만 하고, 저장은 결과를 쓸 때 함
    알고리즘
    1. 같은 알고리즘 버전으로 저장된 값이 있으면 그것을 돌려줌(세션 ID는 None)
    2. 없거나 버전이 다르면, 이 사용자의 세션 중 이 세션보다 앞선(시작 시각이 같거나 이른) 세션에서 업로드된 calibration_glide 녹음 중 가장 최근 것 하나를 고름
    3. 없으면 (None, None)을 돌려줌
    4. 있으면 S3에서 읽어 편한 음높이 범위를 다시 구하고, 구했으면 그 녹음의 세션 ID를 함께 돌려줌
    """
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
    """
    평균 음량 변화의 기준 회차와 그 평가 문장 발화 음량을 찾음
    입력: db (DB 세션), session (계산할 세션 행)
    출력: (기준 회차 세션 ID, 그 회차 평가 문장 발화 음량(dBFS)). 없으면 None
    알고리즘
    1. 같은 사용자·같은 목적에서 이 세션보다 먼저 시작한 done 세션 중, 같은 알고리즘 버전의 서버 mean_level_change 값(평가 문장 음량이 있는 회차)이 있는 세션을 시작 시각·ID 순으로 가장 이른 하나 고름
    2. 그 측정값 세부 정보의 sentence_level_dbfs를 세션 ID와 함께 돌려줌
    """
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
    """
    DB에 쓰지 않고 세션의 서버 지표를 계산함
    입력: db (DB 세션), storage (녹음 저장소, S3), session (계산할 세션 행)
    출력: (계산 결과 SessionMetrics, 다시 구한 캘리브레이션과 그 세션 ID ((최저, 최고) Hz, 세션 ID) 또는 None). 읽기만 함
    알고리즘
    1. 사용자 행과 업로드된 녹음 목록을 읽음
    2. 이 세션에 calibration_glide 녹음이 있으면 저장된 캘리브레이션을 쓰지 않음(계산에서 이 세션 값을 씀). 없으면 calibration_for로 구함
    3. 녹음마다 필요할 때 S3에서 읽는 입력을 만들고 compute_session으로 계산함
    4. 캘리브레이션을 녹음에서 다시 구했으면 그 값과 세션 ID를 함께 돌려줌
    """
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
    """
    세션 행을 잠그고 다시 읽어 아직 processing인지 확인함
    입력: db (DB 세션), session_id (세션 ID)
    출력: 아직 processing이면 잠근 세션 행. 아니면(다른 작업이 먼저 끝냄) 되돌리고 None
    알고리즘
    1. 세션 행을 FOR UPDATE로 잠그고 DB 값으로 다시 읽음
    2. processing이면 그 행을 돌려줌
    3. 아니면 트랜잭션을 되돌리고 정보 기록을 남긴 뒤 None을 돌려줌
    """
    session = db.get(SessionRow, session_id, with_for_update=True, populate_existing=True)
    if session is not None and session.status is SessionStatus.processing:
        return session
    db.rollback()
    logger.info("다른 작업이 먼저 끝낸 세션이라 쓰지 않음: 세션 %s", session_id)
    return None


def save(db: Session, session: SessionRow, result, recomputed) -> int:
    """
    잠근 세션에 계산 결과를 쓰고 done으로 바꿈
    입력: db (DB 세션), session (잠근 세션 행. 처리 작업에서는 processing, 다시 계산(analysis.recompute)에서는 done), result (계산 결과 SessionMetrics), recomputed (다시 구한 캘리브레이션과 그 세션 ID, 또는 None)
    출력: 저장한 서버 지표 수. DB에 서버 측정값 행, 사용자 캘리브레이션, 세션 상태·기준선 표시를 씀(커밋은 부르는 쪽이 함)
    알고리즘
    1. 사용자 행을 세션 다음에 잠금(잠그는 순서는 항상 세션 → 사용자. 교착을 막음)
    2. 다시 구한 캘리브레이션이 있고 같은 버전 저장 값이 없으면 사용자 행에 저장함
    3. 이 세션의 같은 알고리즘 버전 서버 측정값을 지움(다시 계산하는 경우)
    4. 평가 문장 발화 음량이 있고 agc_test 세션이 아니면 평균 음량 변화(mean_level_change)를 더함
    5. 지표마다 단위(METRICS), 알고리즘 버전, 신뢰 낮음, 세부 정보를 붙여 서버 측정값 행으로 넣음
    6. 이 세션 캘리브레이션이 있으면, 같은 버전 저장 값이 없거나 저장 값을 구한 세션이 없거나 그 세션이 이 세션보다 늦지 않게 시작했으면 이 세션 값으로 바꿈
    7. 훈련 세션이고 사용자에게 기준선 세션이 아직 없으면 이 세션을 기준선으로 표시함(사용자의 훈련 세션 중 처음 done이 된 회차)
    8. 상태를 done, 오류를 없음으로 바꾸고 지표 수를 돌려줌
    """
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
    """
    평균 음량 변화를 기준 회차의 평가 문장 발화 음량과 빼서 구함
    입력: db (DB 세션), session (계산할 세션 행), result (계산 결과. sentence_level이 있음)
    출력: MetricValue(mean_level_change, 값(dB), 신뢰 낮음, 세부 정보 sentence_level_dbfs 등)
    알고리즘
    1. 신뢰 낮음은 이 회차 articulation_rate 값 중 하나라도 신뢰 낮음이면 참(잡음 바닥 녹음이 없던 경우)
    2. 기준 회차를 찾음(reference_level). 없으면 이 회차가 기준이라 값 0 dB, 세부 정보 sentence_level_dbfs(소수 셋째 자리)
    3. 있으면 ΔL(평균 음량 변화, dB) = L_sentence(이 회차 평가 문장 발화 프레임 음량 중앙값, dBFS) − L_sentence,ref(기준 회차의 같은 값, dBFS). 세부 정보에 sentence_level_dbfs, reference_session_id, reference_sentence_level_dbfs를 넣음
    """
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
    """
    계산에 실패한 세션을 failed로 바꿈
    입력: db (DB 세션), session_id (세션 ID)
    출력: 바뀐 상태(failed). 다른 작업이 먼저 끝낸 세션이면 None. DB에 상태와 PROCESSING_FAILED 오류를 쓰고 커밋함
    알고리즘
    1. 진행 중인 트랜잭션을 되돌림
    2. 세션을 잠그고 아직 processing인지 확인함(lock_if_processing). 아니면 None을 돌려줌
    3. failed와 PROCESSING_FAILED 오류로 바꿔 커밋함
    """
    db.rollback()
    session = lock_if_processing(db, session_id)
    if session is None:
        return None
    session.status = SessionStatus.failed
    session.error = FAILED
    db.commit()
    return session.status


def process_session(db: Session, storage: RecordingStorage, session_id: UUID) -> SessionStatus | None:
    """
    세션 하나의 서버 지표를 계산해 저장함
    입력: db (DB 세션), storage (녹음 저장소, S3), session_id (세션 ID)
    출력: 바뀐 상태(done 또는 failed). 세션이 없거나 processing이 아니거나 다른 작업이 먼저 끝낸 세션이면 아무것도 쓰지 않고 None
    알고리즘
    1. 세션을 읽고, 없거나 processing이 아니면 None을 돌려줌
    2. 계산함(calculate. DB에는 쓰지 않음)
    3. 세션을 잠가 아직 processing인지 확인하고, 아니면 None을 돌려줌
    4. 결과를 쓰고(save) 커밋함
    5. 2~4단계에서 예외가 나면 오류 기록을 남기고 failed로 바꿈(mark_failed)
    6. 성공하면 지표 수, 걸린 시간(초), 참고 메모를 정보 기록으로 남기고 상태를 돌려줌
    """
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
