"""DB 연결, ORM 모델, 마이그레이션을 로컬 PostgreSQL 테스트 DB로 확인한다."""

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import engine as engine_module
from app.db.models import GameRunRow, MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.schemas.common import Decision, Purpose, RecordingKind, SessionStatus
from app.settings import Settings

TABLES = {"users", "sessions", "recordings", "measurements", "game_runs", "mdc_values"}
NOW = datetime(2026, 11, 20, 9, 15, tzinfo=UTC)


def add_user_and_session(db) -> tuple[UserRow, SessionRow]:
    user = UserRow(id=uuid.uuid4(), created_at=NOW)
    session = SessionRow(
        id=uuid.uuid4(),
        user_id=user.id,
        purpose=Purpose.training,
        started_at=NOW,
        device_model="SM-S921N",
        os_version="Android 16",
        app_version="0.1.0",
        input_device="wired_headset",
        sample_rate=48000,
    )
    db.add_all([user, session])
    db.flush()
    return user, session


def recording(session: SessionRow, kind: RecordingKind = RecordingKind.noise_floor, **fields) -> RecordingRow:
    recording_id = uuid.uuid4()
    data = {
        "id": recording_id,
        "session_id": session.id,
        "kind": kind,
        "s3_key": f"users/{session.user_id}/sessions/{session.id}/{recording_id}.wav",
        "duration_s": 2.0,
        "sample_rate": 48000,
        "size_bytes": 192_044,
    }
    return RecordingRow(**data | fields)


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_sqlalchemy_url_uses_psycopg_driver():
    assert engine_module.sqlalchemy_url("postgresql://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"
    assert engine_module.sqlalchemy_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert engine_module.sqlalchemy_url("postgresql+psycopg://u@host/db") == "postgresql+psycopg://u@host/db"


def test_get_engine_needs_database_url(monkeypatch):
    monkeypatch.setattr(engine_module, "get_settings", lambda: Settings(_env_file=None))
    engine_module.get_engine.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            engine_module.get_engine()
    finally:
        engine_module.get_engine.cache_clear()


def test_models_match_migration(db_engine, migration_config):
    # 모델과 마이그레이션이 어긋나면 command.check가 예외를 낸다.
    command.check(migration_config)


def test_migration_goes_down_and_up(db_engine, migration_config):
    command.downgrade(migration_config, "base")
    assert set(inspect(db_engine).get_table_names()) == {"alembic_version"}

    command.upgrade(migration_config, "head")
    assert set(inspect(db_engine).get_table_names()) == TABLES | {"alembic_version"}


def test_session_rows_and_cascade_delete(db):
    user, session = add_user_and_session(db)
    db.add_all(
        [
            recording(session),
            recording(session, RecordingKind.mpt_trial, trial_no=1),
            MeasurementRow(session_id=session.id, metric="speech_rate", value=3.1, unit="syllables/s", source=MeasurementSource.device),
            MeasurementRow(
                session_id=session.id,
                metric="speech_rate",
                value=3.3,
                unit="syllables/s",
                source=MeasurementSource.server,
                algorithm_version="0.1.0",
            ),
            GameRunRow(
                id=uuid.uuid4(),
                session_id=session.id,
                game="breath_hold",
                level=1,
                round_index=1,
                started_at=NOW,
                success_rate_pct=75.0,
                decision=Decision.up,
            ),
        ]
    )
    db.commit()
    db.refresh(session)

    assert session.status is SessionStatus.open
    assert session.is_baseline is False
    assert session.client_meta == {}
    assert session.created_at.tzinfo is not None
    assert (count(db, RecordingRow), count(db, MeasurementRow), count(db, GameRunRow)) == (2, 2, 1)

    # ORM이 아니라 DB의 ON DELETE CASCADE로 지워지는지 본다(정리 스크립트가 쓰는 방식).
    db.execute(delete(UserRow).where(UserRow.id == user.id))
    db.commit()

    assert [count(db, model) for model in (UserRow, SessionRow, RecordingRow, MeasurementRow, GameRunRow)] == [0] * 5


def test_device_measurement_is_unique_without_algorithm_version(db):
    _, session = add_user_and_session(db)
    for value in (3.1, 3.2):
        db.add(MeasurementRow(session_id=session.id, metric="speech_rate", value=value, unit="syllables/s", source="device"))

    with pytest.raises(IntegrityError, match="uq_measurements"):
        db.commit()


def test_check_constraint_rejects_unknown_purpose(db):
    user, _ = add_user_and_session(db)
    db.commit()

    with pytest.raises(IntegrityError, match="ck_sessions_purpose"):
        db.execute(
            text(
                "INSERT INTO sessions (id, user_id, purpose, started_at, device_model, os_version, app_version, input_device, sample_rate)"
                " VALUES (:id, :user_id, 'practice', now(), 'm', 'o', 'a', 'i', 48000)"
            ),
            {"id": uuid.uuid4(), "user_id": user.id},
        )


def test_s3_key_is_unique(db):
    _, session = add_user_and_session(db)
    first = recording(session)
    db.add_all([first, recording(session, s3_key=first.s3_key)])

    with pytest.raises(IntegrityError, match="uq_recordings_s3_key"):
        db.commit()
