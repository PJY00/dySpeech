"""처리 작업: 로컬 테스트 DB와 moto S3에 합성 녹음을 넣고 서버 지표 계산·저장·상태 전환을 확인한다."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.router import add_startup
from app.db.models import MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.jobs import processing
from app.jobs.processing import MAX_ATTEMPTS, claim_next, process_session
from app.jobs.worker import ProcessingWorker
from app.main import app
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Purpose, RecordingKind, SessionStatus
from app.storage.s3 import recording_key
from fixtures.synthetic import generate

ANSWERS = json.loads(generate.ANSWERS.read_text(encoding="utf-8"))
SESSION_ANSWERS = ANSWERS["session"]["answers"]
START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
FULL_SET = [(item["kind"], item["file"], item.get("trial_no"), item.get("sentence_id")) for item in ANSWERS["session"]["recordings"]]


@pytest.fixture(scope="module")
def wavs(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    assert generate.main(["--out", str(out)]) == 0
    return {path.stem: path.read_bytes() for path in out.glob("*.wav")}


@pytest.fixture
def add_session(db, storage, wavs):
    """처리 대기(processing) 세션을 바로 만든다. 녹음은 complete에서 확인된 것처럼 uploaded_at을 채운다."""

    def add(user_id=None, purpose=Purpose.training, day=0, files=FULL_SET, data=None):
        user_id = user_id or uuid.uuid4()
        if db.get(UserRow, user_id) is None:
            db.add(UserRow(id=user_id, created_at=START))
            db.flush()
        session_id = uuid.uuid4()
        db.add(
            SessionRow(
                id=session_id, user_id=user_id, purpose=purpose, started_at=START + timedelta(days=day),
                ended_at=START + timedelta(days=day, minutes=20), device_model="test", os_version="test",
                app_version="test", input_device="test", sample_rate=48000, status=SessionStatus.processing,
            )
        )
        db.flush()
        for kind, name, trial_no, sentence_id in files:
            recording_id = uuid.uuid4()
            key = recording_key(user_id, session_id, recording_id)
            body = (data or {}).get(name, wavs.get(name))
            storage.client.put_object(Bucket=storage.bucket, Key=key, Body=body)
            db.add(
                RecordingRow(
                    id=recording_id, session_id=session_id, kind=RecordingKind(kind), trial_no=trial_no,
                    sentence_id=sentence_id, s3_key=key, duration_s=1.0, sample_rate=48000, size_bytes=len(body),
                    uploaded_at=START, retain_until=START + timedelta(days=365),
                )
            )
        db.commit()
        return user_id, session_id

    return add


@pytest.fixture
def run_all(db_engine, storage):
    def run() -> int:
        count = 0
        while True:
            with Session(db_engine, expire_on_commit=False) as db:
                session_id = claim_next(db)
                if session_id is None:
                    return count
                process_session(db, storage, session_id)
                count += 1

    return run


def server_values(db, session_id) -> dict[str, MeasurementRow]:
    db.expire_all()
    rows = db.scalars(
        select(MeasurementRow).where(MeasurementRow.session_id == session_id, MeasurementRow.source == MeasurementSource.server)
    ).all()
    return {row.metric: row for row in rows}


def fresh(db, model, key):
    db.expire_all()
    return db.get(model, key)


def near(value: float, expected: dict) -> bool:
    return abs(value - expected["value"]) <= expected["tolerance"]


def test_first_session_is_computed_and_becomes_the_baseline(add_session, run_all, db):
    user_id, session_id = add_session()

    assert run_all() == 1

    session = fresh(db, SessionRow, session_id)
    assert (session.status, session.error, session.processing_attempts, session.is_baseline) == ("done", None, 1, True)
    values = server_values(db, session_id)
    assert set(values) == {m for m in SESSION_ANSWERS if not m.startswith("calibration")}
    for metric, row in values.items():
        assert near(row.value, SESSION_ANSWERS[metric]), (metric, row.value, SESSION_ANSWERS[metric])
        assert row.algorithm_version == ALGORITHM_VERSION and not row.low_reliability
    assert values["mean_level_change"].value == 0.0
    assert "sentence_level_dbfs" in values["mean_level_change"].details
    assert values["max_phonation_time"].details == {"trial": 1}
    calibration = fresh(db, UserRow, user_id).calibration
    assert calibration["algorithm_version"] == ALGORITHM_VERSION and calibration["session_id"] == str(session_id)
    assert near(calibration["f0_low_hz"], SESSION_ANSWERS["calibration_low_hz"])
    assert near(calibration["f0_high_hz"], SESSION_ANSWERS["calibration_high_hz"])


LATER_SET = [(k, "paused_speech_louder" if f == "paused_speech" else f, t, s) for k, f, t, s in FULL_SET if k != "calibration_glide"]


def test_later_session_uses_the_reference_level_and_stored_calibration(add_session, run_all, db):
    user_id, first = add_session()
    _, second = add_session(user_id, day=7, files=LATER_SET)

    assert run_all() == 2

    change = server_values(db, second)["mean_level_change"]
    expected = ANSWERS["files"]["paused_speech_louder"]["mean_level_change_from_paused_speech"]
    assert near(change.value, expected), change.value
    assert change.details["reference_session_id"] == str(first)
    assert not fresh(db, SessionRow, second).is_baseline
    calibration = fresh(db, UserRow, user_id).calibration
    assert calibration["session_id"] == str(first)
    assert server_values(db, second)["f0_median"].details["search_range_hz"] == [75.0, round(calibration["f0_high_hz"] * 1.2, 1)]


def test_calibration_of_another_version_is_not_used_and_is_recomputed(add_session, run_all, db):
    user_id, first = add_session()
    run_all()
    user = db.get(UserRow, user_id)
    user.calibration = {"f0_low_hz": 400.0, "f0_high_hz": 410.0, "algorithm_version": "0.9.0", "session_id": str(first)}
    db.commit()
    _, second = add_session(user_id, day=7, files=LATER_SET)

    run_all()

    calibration = fresh(db, UserRow, user_id).calibration
    assert calibration["algorithm_version"] == ALGORITHM_VERSION
    assert near(calibration["f0_low_hz"], SESSION_ANSWERS["calibration_low_hz"])
    assert server_values(db, second)["f0_median"].details["search_range_hz"][0] == 75.0


def test_newer_calibration_replaces_the_older_one(add_session, run_all, db):
    user_id, _ = add_session()
    _, second = add_session(user_id, day=7)

    run_all()

    assert fresh(db, UserRow, user_id).calibration["session_id"] == str(second)


def test_unreadable_recording_fails_the_session(add_session, run_all, db):
    _, session_id = add_session(files=[("noise_floor", "broken", None, None)], data={"broken": b"not a wav file"})

    run_all()

    session = fresh(db, SessionRow, session_id)
    assert session.status == "failed"
    assert session.error == processing.FAILED
    assert server_values(db, session_id) == {}


def test_missing_parts_still_finish_as_done(add_session, run_all, db):
    _, session_id = add_session(files=[("noise_floor", "noise_floor", None, None)])

    run_all()

    assert fresh(db, SessionRow, session_id).status == "done"
    assert set(server_values(db, session_id)) == {"noise_floor"}


def test_attempt_is_saved_before_computing_and_a_third_start_is_abandoned(add_session, db_engine, storage, db, monkeypatch):
    _, session_id = add_session(files=[("noise_floor", "noise_floor", None, None)])

    def crash(*args):
        raise SystemExit("메모리 부족으로 서버가 죽은 경우를 흉내 낸다")

    monkeypatch.setattr(processing, "calculate", crash)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        with Session(db_engine) as worker_db, pytest.raises(SystemExit):
            process_session(worker_db, storage, claim_next(worker_db))
        session = fresh(db, SessionRow, session_id)
        assert (session.status, session.processing_attempts) == ("processing", attempt)

    with Session(db_engine) as worker_db:
        assert claim_next(worker_db) is None
    session = fresh(db, SessionRow, session_id)
    assert (session.status, session.error, session.processing_attempts) == ("failed", processing.ABANDONED, MAX_ATTEMPTS)


def test_retest_sessions_use_the_first_retest_as_reference(add_session, run_all, db):
    files = [f for f in LATER_SET if f[0] in ("noise_floor", "eval_sentence")]
    quiet = [(k, "paused_speech" if f == "paused_speech_louder" else f, t, s) for k, f, t, s in files]
    user_id, first = add_session(purpose=Purpose.retest, files=quiet)
    _, second = add_session(user_id, purpose=Purpose.retest, day=1, files=files)

    run_all()

    assert server_values(db, first)["mean_level_change"].value == 0.0
    assert server_values(db, second)["mean_level_change"].details["reference_session_id"] == str(first)
    assert not fresh(db, SessionRow, first).is_baseline


def test_agc_session_is_done_without_server_values(add_session, run_all, db):
    _, session_id = add_session(purpose=Purpose.agc_test, files=[("agc_tone", "steady_phonation", None, None)])

    run_all()

    assert fresh(db, SessionRow, session_id).status == "done"
    assert server_values(db, session_id) == {}


def test_processing_again_replaces_values_of_the_same_version(add_session, run_all, db):
    _, session_id = add_session()
    run_all()
    session = db.get(SessionRow, session_id)
    session.status = SessionStatus.processing
    db.commit()

    run_all()

    count = db.scalar(select(func.count()).select_from(MeasurementRow).where(MeasurementRow.session_id == session_id))
    assert count == len(SESSION_ANSWERS) - 2  # 캘리브레이션 두 값은 측정 기록이 아니다
    assert fresh(db, SessionRow, session_id).processing_attempts == 2


def test_sessions_are_processed_oldest_first(add_session, db_engine, db):
    _, later = add_session(day=3, files=[("noise_floor", "noise_floor", None, None)])
    _, earlier = add_session(day=1, files=[("noise_floor", "noise_floor", None, None)])

    with Session(db_engine) as worker_db:
        assert claim_next(worker_db) == earlier


def test_worker_thread_processes_pending_sessions(add_session, db_engine, storage, db):
    _, session_id = add_session(files=[("noise_floor", "noise_floor", None, None)])
    worker = ProcessingWorker(lambda: Session(db_engine, expire_on_commit=False), lambda: storage, idle_s=0.2)

    worker.start()
    try:
        deadline = time.monotonic() + 20
        while fresh(db, SessionRow, session_id).status == "processing" and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        worker.stop()

    assert fresh(db, SessionRow, session_id).status == "done"


def test_complete_wakes_the_worker(flow_client, storage):
    class Recorder:
        woken = 0

        def wake(self):
            self.woken += 1

    user_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow_client.put(f"/v1/users/{user_id}", json={"created_at": "2026-11-20T09:00:00Z"})
    body = {
        "user_id": user_id, "purpose": "agc_test", "started_at": "2026-11-20T09:00:00Z", "device_model": "t",
        "os_version": "t", "app_version": "t", "input_device": "t", "sample_rate": 48000,
    }
    flow_client.put(f"/v1/sessions/{session_id}", json=body)
    app.state.processing_worker = Recorder()
    try:
        response = flow_client.post(f"/v1/sessions/{session_id}/complete", json={"ended_at": "2026-11-20T09:10:00Z"})
        assert response.status_code == 202
        assert app.state.processing_worker.woken == 1
    finally:
        del app.state.processing_worker


def test_startup_starts_and_stops_the_worker():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.settings import OPERATING_VARIABLES, Settings

    events = []

    class Fake:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    test_app = FastAPI()
    settings = Settings(_env_file=None, **{attribute: "x" for attribute in OPERATING_VARIABLES.values()})
    add_startup(test_app, lambda: settings, Fake)

    with TestClient(test_app):
        assert events == ["start"]
        assert isinstance(test_app.state.processing_worker, Fake)
    assert events == ["start", "stop"]


# ---- 두 작업이 같은 세션을 계산할 때(배포 중 새 서버와 이전 서버가 잠깐 함께 도는 경우) ----

NOISE_AND_TRIALS = [f for f in FULL_SET if f[0] in ("noise_floor", "mpt_trial")]


def claim_twice(db_engine, session_id):
    """두 작업이 차례로 같은 세션을 잡는다(시도 횟수 저장 뒤 잠금이 풀리므로 둘 다 잡힌다)."""
    claims = []
    for _ in range(2):
        with Session(db_engine) as worker_db:
            claims.append(claim_next(worker_db))
    assert claims == [session_id, session_id]


def run_in_thread(target):
    import threading

    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", target()), name="late-worker")
    thread.start()
    return thread, box


def late_calculate(monkeypatch, release, fail=False):
    """늦은 작업(late-worker 스레드)의 계산이 먼저 끝난 작업 뒤에 끝나게 한다."""
    import threading

    original = processing.calculate

    def calculate(db, storage, session):
        result = original(db, storage, session)
        if threading.current_thread().name == "late-worker":
            assert release.wait(30)
            if fail:
                raise RuntimeError("늦은 작업의 계산 오류")
        return result

    monkeypatch.setattr(processing, "calculate", calculate)


@pytest.mark.parametrize("late_fails", [False, True], ids=["late-finishes", "late-raises"])
def test_late_worker_does_not_overwrite_a_finished_session(add_session, db_engine, storage, db, monkeypatch, late_fails):
    import threading

    _, session_id = add_session(files=NOISE_AND_TRIALS)
    claim_twice(db_engine, session_id)
    release = threading.Event()
    late_calculate(monkeypatch, release, fail=late_fails)

    def late():
        with Session(db_engine, expire_on_commit=False) as late_db:
            return process_session(late_db, storage, session_id)

    thread, box = run_in_thread(late)
    with Session(db_engine, expire_on_commit=False) as early_db:
        assert process_session(early_db, storage, session_id) == SessionStatus.done
    release.set()
    thread.join(30)

    assert box["result"] is None
    session = fresh(db, SessionRow, session_id)
    assert (session.status, session.error, session.processing_attempts) == ("done", None, 2)
    assert set(server_values(db, session_id)) == {"noise_floor", "max_phonation_time", "sustained_level", "loudness_decay_slope"}


def test_writer_waits_for_the_lock_and_then_skips(add_session, db_engine, storage, db):
    _, session_id = add_session(files=[("noise_floor", "noise_floor", None, None)])
    claim_twice(db_engine, session_id)
    holder = Session(db_engine, expire_on_commit=False)
    locked = processing.lock_if_processing(holder, session_id)

    def late():
        with Session(db_engine, expire_on_commit=False) as late_db:
            return process_session(late_db, storage, session_id)

    thread, box = run_in_thread(late)
    thread.join(1.0)
    assert thread.is_alive(), "잠금을 기다려야 한다"
    locked.status = SessionStatus.done
    holder.commit()
    holder.close()
    thread.join(30)

    assert box["result"] is None
    assert fresh(db, SessionRow, session_id).status == "done"
    assert server_values(db, session_id) == {}
