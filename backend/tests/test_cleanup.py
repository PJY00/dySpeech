"""정리 스크립트를 로컬 테스트 DB와 moto S3로 확인한다."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import MeasurementRow, RecordingRow, SessionRow, UserRow
from app.schemas.common import Purpose, RecordingKind
from app.storage.s3 import recording_key
from tools import cleanup

NOW = datetime(2026, 11, 20, 9, 15, tzinfo=UTC)


def add_user_with_session(db, storage, recordings: int = 2) -> tuple[uuid.UUID, uuid.UUID, list[str]]:
    """사용자·세션·녹음 기록과, 같은 위치의 S3 객체를 만든다."""
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    db.add(UserRow(id=user_id, created_at=NOW))
    db.add(
        SessionRow(
            id=session_id,
            user_id=user_id,
            purpose=Purpose.training,
            started_at=NOW,
            device_model="m",
            os_version="o",
            app_version="a",
            input_device="i",
            sample_rate=48000,
        )
    )
    db.flush()
    keys = []
    for _ in range(recordings):
        recording_id = uuid.uuid4()
        key = recording_key(user_id, session_id, recording_id)
        db.add(
            RecordingRow(
                id=recording_id,
                session_id=session_id,
                kind=RecordingKind.noise_floor,
                s3_key=key,
                duration_s=2.0,
                sample_rate=48000,
                size_bytes=4,
            )
        )
        storage.client.put_object(Bucket=storage.bucket, Key=key, Body=b"RIFF")
        keys.append(key)
    db.add(MeasurementRow(session_id=session_id, metric="speech_rate", value=3.0, unit="syllables/s", source="device"))
    db.commit()
    return user_id, session_id, keys


def write_targets(tmp_path, **fields) -> str:
    path = tmp_path / "ids.json"
    path.write_text(json.dumps({key: [str(v) for v in values] for key, values in fields.items()}), encoding="utf-8")
    return str(path)


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_preview_deletes_nothing(db, storage, tmp_path, capsys):
    user_id, _, keys = add_user_with_session(db, storage)
    # DB에는 없고 S3에만 남은 객체(업로드만 되고 등록 전에 멈춘 경우)
    stray = f"users/{user_id}/sessions/x/stray.wav"
    storage.client.put_object(Bucket=storage.bucket, Key=stray, Body=b"RIFF")

    assert cleanup.main([write_targets(tmp_path, user_ids=[user_id])], db=db, storage=storage) == 0

    assert "사용자 1명, 세션 1개, 녹음 기록 2개, S3 객체 3개" in capsys.readouterr().out
    assert count(db, UserRow) == 1
    assert sorted(storage.keys_under(f"users/{user_id}/")) == sorted([*keys, stray])


def test_yes_deletes_only_listed_targets(db, storage, tmp_path):
    mine, _, _ = add_user_with_session(db, storage)
    other, other_session, other_keys = add_user_with_session(db, storage)

    assert cleanup.main([write_targets(tmp_path, user_ids=[mine]), "--yes"], db=db, storage=storage) == 0

    db.expire_all()
    assert list(db.scalars(select(UserRow.id))) == [other]
    assert list(db.scalars(select(SessionRow.id))) == [other_session]
    assert count(db, MeasurementRow) == 1
    assert storage.keys_under(f"users/{mine}/") == []
    assert sorted(storage.keys_under(f"users/{other}/")) == sorted(other_keys)


def test_session_only_target_keeps_the_user(db, storage, tmp_path):
    user_id, session_id, _ = add_user_with_session(db, storage)

    cleanup.main([write_targets(tmp_path, session_ids=[session_id]), "--yes"], db=db, storage=storage)

    db.expire_all()
    assert list(db.scalars(select(UserRow.id))) == [user_id]
    assert count(db, SessionRow) == 0
    assert count(db, RecordingRow) == 0
    assert storage.keys_under(f"users/{user_id}/") == []


def test_s3_failure_keeps_db_rows(db, storage, tmp_path, monkeypatch):
    user_id, _, _ = add_user_with_session(db, storage)

    def fail(keys):
        raise RuntimeError("S3 객체를 지우지 못했습니다")

    monkeypatch.setattr(storage, "delete", fail)
    with pytest.raises(RuntimeError):
        cleanup.main([write_targets(tmp_path, user_ids=[user_id]), "--yes"], db=db, storage=storage)

    db.rollback()
    assert count(db, UserRow) == 1


@pytest.mark.parametrize(
    "content",
    [
        {"user_ids": ["not-a-uuid"]},
        {},
        {"user_ids": [str(uuid.uuid4())], "s3_keys": [f"users/{uuid.uuid4()}/x.wav"]},
    ],
)
def test_bad_target_files_are_refused(tmp_path, content, capsys):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    assert cleanup.main([str(path)], db=object(), storage=object()) == 2
    assert "목록 파일을 쓸 수 없습니다" in capsys.readouterr().err
