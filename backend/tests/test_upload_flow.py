"""로컬 PostgreSQL과 moto S3로 확인하는 실제 처리: 등록, 업로드 URL, complete, 상태, 진전도, 녹음 열람."""

import io
import logging
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.config.metrics import PROGRESS_METRICS
from app.db.models import GameRunRow, MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.main import app
from app.schemas.history import History
from app.schemas.recordings import UploadUrlsOut
from app.schemas.sessions import SessionDetail
from app.storage.s3 import RECORDING_RETENTION, recording_key

STARTED_AT = datetime(2026, 11, 20, 9, 15, tzinfo=UTC)


def wav(seconds: float = 0.05, rate: int = 48000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(width)
        file.setframerate(rate)
        file.writeframes(bytes(round(rate * seconds) * channels * width))
    return buffer.getvalue()


def session_body(user_id: str, **changes) -> dict:
    body = {
        "user_id": user_id,
        "purpose": "training",
        "started_at": STARTED_AT.isoformat(),
        "device_model": "SM-S921N",
        "os_version": "Android 16",
        "app_version": "0.2.0",
        "input_device": "wired_headset",
        "sample_rate": 48000,
    }
    return body | changes


def register(client, user_id: str | None = None, **session_changes) -> tuple[str, str]:
    user_id = user_id or str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    assert client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T09:00:00Z"}).status_code == 200
    response = client.put(f"/v1/sessions/{session_id}", json=session_body(user_id, **session_changes))
    assert response.status_code == 200, response.text
    return user_id, session_id


def recording(kind: str = "noise_floor", **fields) -> dict:
    extra = {"mpt_trial": {"trial_no": 1}, "eval_sentence": {"sentence_id": "s1"}}.get(kind, {})
    return {
        "recording_id": str(uuid.uuid4()),
        "kind": kind,
        "duration_s": 0.05,
        "sample_rate": 48000,
        "size_bytes": 4844,
        **extra,
        **fields,
    }


def request_urls(client, session_id: str, recordings: list[dict]):
    return client.post(f"/v1/sessions/{session_id}/upload-urls", json={"recordings": recordings})


def key_of(url: str) -> str:
    return unquote(urlparse(url).path.lstrip("/"))


def upload(storage, url: str, data: bytes) -> None:
    """presigned URL이 가리키는 위치에 올린다. moto는 HTTP 요청을 받지 않으므로 같은 위치에 직접 넣는다."""
    storage.client.put_object(Bucket=storage.bucket, Key=key_of(url), Body=data, ContentType="audio/wav")


def upload_all(client, storage, session_id: str, recordings: list[dict], data: bytes | None = None) -> UploadUrlsOut:
    response = request_urls(client, session_id, recordings)
    assert response.status_code == 200, response.text
    out = UploadUrlsOut.model_validate(response.json())
    for item in out.uploads:
        upload(storage, item.url, data or wav())
    return out


def complete_body(**changes) -> dict:
    return {"ended_at": "2026-11-20T09:40:00Z"} | changes


def complete(client, session_id: str, **changes):
    return client.post(f"/v1/sessions/{session_id}/complete", json=complete_body(**changes))


def game_run(game_run_id: str | None = None) -> dict:
    return {
        "game_run_id": game_run_id or str(uuid.uuid4()),
        "game": "breath_hold",
        "level": 3,
        "round_index": 1,
        "started_at": "2026-11-20T09:30:00Z",
        "params": {"target_s": 8},
        "success_rate_pct": 75.0,
        "decision": "up",
        "decision_reason": "성공률 70% 이상",
        "result": {"best_s": 9.1},
    }


def error_code(response) -> str:
    return response.json()["error"]["code"]


def fresh(db, statement):
    db.expire_all()
    return db.scalars(statement).all()


# ---- 사용자 등록 ----


def test_put_user_is_idempotent_and_answers_in_utc(flow_client):
    user_id = str(uuid.uuid4())

    first = flow_client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T18:15:00+09:00"})
    again = flow_client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T09:15:00Z"})

    assert first.status_code == again.status_code == 200
    assert first.json() == again.json() == {"id": user_id, "created_at": "2026-11-20T09:15:00Z"}


def test_put_user_with_other_created_at_is_409(flow_client, db):
    user_id = str(uuid.uuid4())
    flow_client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T09:15:00Z"})

    response = flow_client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-21T09:15:00Z"})

    assert response.status_code == 409
    assert error_code(response) == "ID_CONFLICT"
    assert fresh(db, select(UserRow.created_at)) == [datetime(2026, 11, 20, 9, 15, tzinfo=UTC)]


def test_same_user_sent_at_once_is_saved_once(flow_client, db):
    user_id = str(uuid.uuid4())

    def send(_):
        # 요청마다 다른 클라이언트로 동시에 보낸다. with 없이 만들어 서버 시작 단계는 거치지 않는다.
        client = TestClient(app, headers=flow_client.headers)
        return client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T09:15:00Z"}).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(send, range(6)))

    assert statuses == [200] * 6
    assert len(fresh(db, select(UserRow))) == 1


# ---- 세션 등록 ----


def test_put_session_needs_registered_user(flow_client):
    response = flow_client.put(f"/v1/sessions/{uuid.uuid4()}", json=session_body(str(uuid.uuid4())))

    assert response.status_code == 404
    assert error_code(response) == "USER_NOT_FOUND"


def test_put_session_saves_and_returns_open_session(flow_client, db):
    user_id, _ = register(flow_client)
    session_id = str(uuid.uuid4())

    response = flow_client.put(
        f"/v1/sessions/{session_id}", json=session_body(user_id, client_meta={"build": "debug"}) | {"started_at": "2026-11-20T18:15:00+09:00"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": session_id,
        "user_id": user_id,
        "purpose": "training",
        "started_at": "2026-11-20T09:15:00Z",
        "ended_at": None,
        "device_model": "SM-S921N",
        "os_version": "Android 16",
        "app_version": "0.2.0",
        "input_device": "wired_headset",
        "sample_rate": 48000,
        "status": "open",
        "is_baseline": False,
    }
    row = fresh(db, select(SessionRow).where(SessionRow.id == uuid.UUID(session_id)))[0]
    assert row.client_meta == {"build": "debug"}


def test_put_session_repeat_is_200_and_keeps_first_client_meta(flow_client, db):
    user_id, _ = register(flow_client)
    session_id = str(uuid.uuid4())
    first = flow_client.put(f"/v1/sessions/{session_id}", json=session_body(user_id, client_meta={"try": 1}))

    again = flow_client.put(f"/v1/sessions/{session_id}", json=session_body(user_id, client_meta={"try": 2}))

    assert again.status_code == 200
    assert again.json() == first.json()
    assert fresh(db, select(SessionRow.client_meta).where(SessionRow.id == uuid.UUID(session_id))) == [{"try": 1}]


@pytest.mark.parametrize(
    ("field", "value"),
    [("purpose", "retest"), ("started_at", "2026-11-21T09:15:00Z"), ("sample_rate", 16000), ("app_version", "0.3.0")],
)
def test_put_session_with_other_content_is_409(flow_client, field, value):
    user_id, session_id = register(flow_client)

    response = flow_client.put(f"/v1/sessions/{session_id}", json=session_body(user_id, **{field: value}))

    assert response.status_code == 409
    assert error_code(response) == "ID_CONFLICT"
    assert field in response.json()["error"]["message"]


def test_session_cannot_move_to_another_user(flow_client):
    _, session_id = register(flow_client)
    other_user, _ = register(flow_client)

    response = flow_client.put(f"/v1/sessions/{session_id}", json=session_body(other_user))

    assert response.status_code == 409


# ---- 업로드 URL ----


def test_upload_urls_needs_session(flow_client):
    response = request_urls(flow_client, str(uuid.uuid4()), [recording()])

    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_upload_urls_point_to_server_chosen_keys(flow_client, db):
    user_id, session_id = register(flow_client)
    recordings = [recording("noise_floor"), recording("mpt_trial", trial_no=2), recording("eval_sentence")]
    before = datetime.now(UTC)

    response = request_urls(flow_client, session_id, recordings)

    assert response.status_code == 200
    uploads = UploadUrlsOut.model_validate(response.json()).uploads
    assert [str(u.recording_id) for u in uploads] == [r["recording_id"] for r in recordings]
    for item in uploads:
        assert key_of(item.url) == recording_key(uuid.UUID(user_id), uuid.UUID(session_id), item.recording_id)
        assert item.method == "PUT"
        assert item.headers == {"Content-Type": "audio/wav"}
        assert before + timedelta(minutes=15) <= item.expires_at <= datetime.now(UTC) + timedelta(minutes=15)
    rows = {row.id: row for row in fresh(db, select(RecordingRow))}
    trial = rows[uuid.UUID(recordings[1]["recording_id"])]
    assert (trial.kind, trial.trial_no, trial.duration_s, trial.sample_rate, trial.size_bytes) == ("mpt_trial", 2, 0.05, 48000, 4844)
    assert rows[uuid.UUID(recordings[2]["recording_id"])].sentence_id == "s1"
    assert all(row.uploaded_at is None for row in rows.values())


def test_upload_urls_repeat_gives_new_url_for_same_place(flow_client, db):
    _, session_id = register(flow_client)
    recordings = [recording(), recording("agc_tone", agc_signal="agc_hold")]
    first = UploadUrlsOut.model_validate(request_urls(flow_client, session_id, recordings).json())

    again = request_urls(flow_client, session_id, recordings)

    assert again.status_code == 200
    second = UploadUrlsOut.model_validate(again.json())
    assert [key_of(u.url) for u in second.uploads] == [key_of(u.url) for u in first.uploads]
    assert len(fresh(db, select(RecordingRow))) == 2


@pytest.mark.parametrize(
    "change", [{"kind": "calibration_glide"}, {"kind": "mpt_trial", "trial_no": 1}], ids=["kind", "kind-and-field"]
)
def test_upload_urls_with_other_recording_content_is_409_and_saves_nothing(flow_client, db, change):
    _, session_id = register(flow_client)
    existing = recording()
    request_urls(flow_client, session_id, [existing])
    new = recording()

    response = request_urls(flow_client, session_id, [new, existing | change])

    assert response.status_code == 409
    assert error_code(response) == "ID_CONFLICT"
    assert existing["recording_id"] in response.json()["error"]["message"]
    assert [str(row.id) for row in fresh(db, select(RecordingRow))] == [existing["recording_id"]]


def test_recording_id_of_another_session_is_409(flow_client):
    _, first_session = register(flow_client)
    _, second_session = register(flow_client)
    shared = recording()
    request_urls(flow_client, first_session, [shared])

    response = request_urls(flow_client, second_session, [shared])

    assert response.status_code == 409


def test_file_fields_may_change_before_complete(flow_client, db):
    # WAV 규격 422를 받은 뒤 같은 recording_id로 고친 파일을 올리는 경우
    _, session_id = register(flow_client)
    item = recording()
    request_urls(flow_client, session_id, [item])

    response = request_urls(flow_client, session_id, [item | {"sample_rate": 16000, "size_bytes": 1644, "duration_s": 0.051}])

    assert response.status_code == 200
    row = fresh(db, select(RecordingRow))[0]
    assert (row.sample_rate, row.size_bytes, row.duration_s) == (16000, 1644, 0.051)


# ---- complete ----


def test_complete_needs_session(flow_client):
    response = complete(flow_client, str(uuid.uuid4()))

    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_complete_lists_recordings_not_uploaded(flow_client, storage, db):
    _, session_id = register(flow_client)
    recordings = [recording(), recording("mpt_trial"), recording("eval_sentence")]
    out = UploadUrlsOut.model_validate(request_urls(flow_client, session_id, recordings).json())
    upload(storage, out.uploads[1].url, wav())

    response = complete(flow_client, session_id)

    assert response.status_code == 409
    assert error_code(response) == "MISSING_RECORDINGS"
    missing = response.json()["error"]["missing_recording_ids"]
    assert sorted(missing) == sorted([recordings[0]["recording_id"], recordings[2]["recording_id"]])
    assert fresh(db, select(SessionRow.status)) == ["open"]


def test_complete_reports_each_file_that_breaks_the_format(flow_client, storage, db, caplog):
    _, session_id = register(flow_client)
    good, stereo, wrong_rate = recording(), recording("mpt_trial"), recording("eval_sentence")
    out = UploadUrlsOut.model_validate(request_urls(flow_client, session_id, [good, stereo, wrong_rate]).json())
    upload(storage, out.uploads[0].url, wav())
    upload(storage, out.uploads[1].url, wav(channels=2))
    upload(storage, out.uploads[2].url, wav(rate=44100))

    with caplog.at_level(logging.INFO, logger="app.api.sessions"):
        response = complete(flow_client, session_id)

    assert "complete 거부(422): 규격과 다른 녹음 2개" in caplog.text
    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("INVALID_RECORDING_FORMAT", "녹음 파일이 규격과 다릅니다")
    assert sorted(error["invalid_recordings"], key=lambda r: r["recording_id"]) == sorted(
        [
            {"recording_id": stereo["recording_id"], "reason": "채널이 2개입니다. 모노(1개)여야 합니다"},
            {
                "recording_id": wrong_rate["recording_id"],
                "reason": "업로드 URL 요청 때 알린 샘플레이트(48000 Hz)와 파일의 샘플레이트(44100 Hz)가 다릅니다",
            },
        ],
        key=lambda r: r["recording_id"],
    )
    assert fresh(db, select(SessionRow.status)) == ["open"]


def test_missing_recordings_are_checked_before_the_format(flow_client, storage):
    _, session_id = register(flow_client)
    out = UploadUrlsOut.model_validate(request_urls(flow_client, session_id, [recording(), recording("mpt_trial")]).json())
    upload(storage, out.uploads[0].url, wav(channels=2))

    response = complete(flow_client, session_id)

    assert response.status_code == 409
    assert response.json()["error"]["missing_recording_ids"] == [str(out.uploads[1].recording_id)]


def test_fixed_file_with_the_same_recording_id_is_accepted(flow_client, storage):
    _, session_id = register(flow_client)
    item = recording()
    out = upload_all(flow_client, storage, session_id, [item], wav(rate=44100))
    assert error_code(complete(flow_client, session_id)) == "INVALID_RECORDING_FORMAT"

    # 앱이 파일을 다시 만든다. 이번에는 선언한 샘플레이트를 파일에 맞춘다.
    again = upload_all(flow_client, storage, session_id, [item | {"sample_rate": 44100}], wav(rate=44100))
    response = complete(flow_client, session_id)

    assert key_of(again.uploads[0].url) == key_of(out.uploads[0].url)
    assert response.status_code == 202
    assert response.json() == {"session_id": session_id, "status": "processing"}


def test_complete_saves_device_values_game_runs_and_upload_times(flow_client, storage, db):
    _, session_id = register(flow_client)
    run = game_run()
    items = [recording(), recording("game", game_run_id=run["game_run_id"])]
    upload_all(flow_client, storage, session_id, items)
    before = datetime.now(UTC)

    response = complete(
        flow_client,
        session_id,
        ended_at="2026-11-20T18:40:00+09:00",
        device_measurements=[
            {"metric": "max_phonation_time", "value": 12.1, "unit": "s"},
            {"metric": "noise_floor", "value": -61.0, "unit": "dBFS", "low_reliability": True},
        ],
        game_runs=[run],
        client_meta={"retry_count": 0},
    )

    assert response.status_code == 202
    session = fresh(db, select(SessionRow))[0]
    assert session.status == "processing"
    assert session.ended_at == datetime(2026, 11, 20, 9, 40, tzinfo=UTC)
    assert session.complete_client_meta == {"retry_count": 0}
    measurements = {m.metric: m for m in fresh(db, select(MeasurementRow))}
    assert {name: (m.value, m.unit, m.source, m.algorithm_version, m.low_reliability) for name, m in measurements.items()} == {
        "max_phonation_time": (12.1, "s", MeasurementSource.device, None, False),
        "noise_floor": (-61.0, "dBFS", MeasurementSource.device, None, True),
    }
    saved_run = fresh(db, select(GameRunRow))[0]
    assert (str(saved_run.id), saved_run.game, saved_run.level, saved_run.success_rate_pct) == (run["game_run_id"], "breath_hold", 3, 75.0)
    assert (saved_run.params, saved_run.result, saved_run.decision) == ({"target_s": 8}, {"best_s": 9.1}, "up")
    for row in fresh(db, select(RecordingRow)):
        assert before <= row.uploaded_at <= datetime.now(UTC)
        assert row.retain_until == storage.info(row.s3_key).last_modified + RECORDING_RETENTION


def test_complete_again_returns_status_without_saving_twice(flow_client, storage, db):
    _, session_id = register(flow_client)
    upload_all(flow_client, storage, session_id, [recording()])
    measurement = {"metric": "speech_rate", "value": 3.1, "unit": "syllables/s"}
    complete(flow_client, session_id, device_measurements=[measurement], game_runs=[game_run()])

    again = complete(flow_client, session_id, device_measurements=[measurement | {"value": 9.9}], game_runs=[game_run()])

    assert again.status_code == 202
    assert again.json() == {"session_id": session_id, "status": "processing"}
    assert [m.value for m in fresh(db, select(MeasurementRow))] == [3.1]
    assert len(fresh(db, select(GameRunRow))) == 1


def test_complete_sent_at_once_is_saved_once(flow_client, storage, db):
    _, session_id = register(flow_client)
    upload_all(flow_client, storage, session_id, [recording(), recording("mpt_trial")])
    body = complete_body(device_measurements=[{"metric": "speech_rate", "value": 3.1, "unit": "syllables/s"}])

    def send(_):
        return TestClient(app, headers=flow_client.headers).post(f"/v1/sessions/{session_id}/complete", json=body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(4)))

    assert [(r.status_code, r.json()["status"]) for r in responses] == [(202, "processing")] * 4
    assert db.scalar(select(func.count()).select_from(MeasurementRow)) == 1


def test_game_recording_needs_its_game_run(flow_client, storage, db):
    _, session_id = register(flow_client)
    game_run_id = str(uuid.uuid4())
    upload_all(flow_client, storage, session_id, [recording("game", game_run_id=game_run_id)])

    response = complete(flow_client, session_id, game_runs=[game_run()])

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"] == [{"path": "body.game_runs", "reason": f"game 녹음의 game_run_id가 game_runs에 없습니다: {game_run_id}"}]
    assert fresh(db, select(SessionRow.status)) == ["open"]


def test_game_run_id_of_another_session_is_422(flow_client, storage):
    run = game_run()
    _, first = register(flow_client)
    upload_all(flow_client, storage, first, [recording()])
    complete(flow_client, first, game_runs=[run])
    _, second = register(flow_client)
    upload_all(flow_client, storage, second, [recording()])

    response = complete(flow_client, second, game_runs=[game_run(), run])

    assert response.status_code == 422
    assert [f["path"] for f in response.json()["error"]["fields"]] == ["body.game_runs[1].game_run_id"]


def test_after_complete_recordings_cannot_change(flow_client, storage):
    _, session_id = register(flow_client)
    item = recording()
    upload_all(flow_client, storage, session_id, [item])
    complete(flow_client, session_id)

    same = request_urls(flow_client, session_id, [item])
    changed = request_urls(flow_client, session_id, [item | {"size_bytes": 9999}])

    # 처음부터 다시 보내는 재시도는 그대로 되고, 파일 내용을 바꾸려는 요청은 막는다.
    assert same.status_code == 200
    assert changed.status_code == 409


def test_new_recording_after_complete_is_registered_and_logged(flow_client, storage, caplog):
    _, session_id = register(flow_client)
    upload_all(flow_client, storage, session_id, [recording()])
    complete(flow_client, session_id)

    with caplog.at_level(logging.WARNING, logger="app.api.sessions"):
        response = request_urls(flow_client, session_id, [recording("mpt_trial")])

    assert response.status_code == 200
    assert "complete를 받은 세션(상태 processing)에 새 녹음 1개가 등록됨" in caplog.text


# ---- 처리 상태 ----


def test_get_session_needs_session(flow_client):
    response = flow_client.get(f"/v1/sessions/{uuid.uuid4()}")

    assert response.status_code == 404
    assert error_code(response) == "SESSION_NOT_FOUND"


def test_get_session_shows_processing_without_server_values(flow_client, storage):
    _, session_id = register(flow_client)
    upload_all(flow_client, storage, session_id, [recording()])
    complete(flow_client, session_id, device_measurements=[{"metric": "speech_rate", "value": 3.1, "unit": "syllables/s"}])

    response = flow_client.get(f"/v1/sessions/{session_id}")

    assert response.status_code == 200
    detail = SessionDetail.model_validate(response.json())
    assert (detail.status, detail.ended_at, detail.server_measurements, detail.error) == (
        "processing",
        datetime(2026, 11, 20, 9, 40, tzinfo=UTC),
        [],
        None,
    )


def test_get_session_lists_server_values_in_metric_order_and_error(flow_client, db):
    _, session_id = register(flow_client)
    sid = uuid.UUID(session_id)
    db.add_all(
        [
            MeasurementRow(session_id=sid, metric="noise_floor", value=-60.0, unit="dBFS", source=MeasurementSource.server, algorithm_version="1.0.0"),
            MeasurementRow(session_id=sid, metric="max_phonation_time", value=12.0, unit="s", source=MeasurementSource.server, algorithm_version="1.0.0", low_reliability=True),
            MeasurementRow(session_id=sid, metric="max_phonation_time", value=11.0, unit="s", source=MeasurementSource.device),
        ]
    )
    db.execute(update(SessionRow).values(status="failed", error={"code": "NO_VOICE", "message": "발성을 찾지 못했습니다"}))
    db.commit()

    detail = flow_client.get(f"/v1/sessions/{session_id}").json()

    assert [(m["metric"], m["value"], m["low_reliability"]) for m in detail["server_measurements"]] == [
        ("max_phonation_time", 12.0, True),
        ("noise_floor", -60.0, False),
    ]
    assert detail["status"] == "failed"
    assert detail["error"] == {"code": "NO_VOICE", "message": "발성을 찾지 못했습니다"}


# ---- 진전도 ----


def test_history_needs_user(flow_client):
    response = flow_client.get(f"/v1/users/{uuid.uuid4()}/history")

    assert response.status_code == 404
    assert error_code(response) == "USER_NOT_FOUND"


def test_history_is_no_data_and_gives_recent_device_values(flow_client, storage):
    user_id = str(uuid.uuid4())
    completed = []
    for day in range(4):
        _, session_id = register(flow_client, user_id, started_at=(STARTED_AT + timedelta(days=day)).isoformat())
        upload_all(flow_client, storage, session_id, [recording()])
        complete(
            flow_client,
            session_id,
            device_measurements=[
                {"metric": "noise_floor", "value": -60.0 - day, "unit": "dBFS"},
                {"metric": "max_phonation_time", "value": 10.0 + day, "unit": "s"},
            ],
        )
        completed.append(session_id)
    # 반복 측정 세션, complete 전 세션, 기기 값이 없는 세션은 넣지 않는다.
    _, retest = register(flow_client, user_id, purpose="retest", started_at=(STARTED_AT + timedelta(days=9)).isoformat())
    upload_all(flow_client, storage, retest, [recording()])
    complete(flow_client, retest, device_measurements=[{"metric": "max_phonation_time", "value": 99.0, "unit": "s"}])
    register(flow_client, user_id, started_at=(STARTED_AT + timedelta(days=10)).isoformat())
    _, empty = register(flow_client, user_id, started_at=(STARTED_AT + timedelta(days=11)).isoformat())
    upload_all(flow_client, storage, empty, [recording()])
    complete(flow_client, empty)

    response = flow_client.get(f"/v1/users/{user_id}/history")

    assert response.status_code == 200
    history = History.model_validate(response.json())
    assert [m.metric for m in history.metrics] == PROGRESS_METRICS
    assert all(m.status == "no_data" and m.baseline is None and m.series == [] for m in history.metrics)
    recent = history.recent_device_measurements
    assert [str(s.session_id) for s in recent] == completed[1:]
    assert [[(m.metric, m.value) for m in s.measurements] for s in recent] == [
        [("max_phonation_time", 10.0 + day), ("noise_floor", -60.0 - day)] for day in (1, 2, 3)
    ]


# ---- 녹음 열람 ----


def test_recording_url_needs_completed_recording(flow_client, storage, db):
    user_id, session_id = register(flow_client)
    item = recording()
    upload_all(flow_client, storage, session_id, [item])
    path = f"/v1/recordings/{item['recording_id']}/url"

    unknown = flow_client.get(f"/v1/recordings/{uuid.uuid4()}/url")
    before_complete = flow_client.get(path)
    complete(flow_client, session_id)
    after_complete = flow_client.get(path)

    assert (unknown.status_code, before_complete.status_code, after_complete.status_code) == (404, 404, 200)
    assert error_code(before_complete) == "RECORDING_NOT_FOUND"
    body = after_complete.json()
    assert key_of(body["url"]) == recording_key(uuid.UUID(user_id), uuid.UUID(session_id), uuid.UUID(item["recording_id"]))
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=9) < expires_at - datetime.now(UTC) <= timedelta(minutes=10)


def test_recording_url_is_404_after_retention(flow_client, storage, db):
    _, session_id = register(flow_client)
    item = recording()
    upload_all(flow_client, storage, session_id, [item])
    complete(flow_client, session_id)
    db.execute(update(RecordingRow).values(retain_until=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()

    response = flow_client.get(f"/v1/recordings/{item['recording_id']}/url")

    assert response.status_code == 404
    assert error_code(response) == "RECORDING_NOT_FOUND"
