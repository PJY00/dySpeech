"""다시 계산 도구: 로컬 테스트 DB와 moto S3에서 이전 알고리즘 버전 값만 있는 세션을 지금 버전으로 다시 계산한다."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from analysis import recompute
from app.db.models import MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.jobs.processing import claim_next, process_session
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Purpose, RecordingKind, SessionStatus
from app.storage.s3 import recording_key
from fixtures.synthetic import generate

ANSWERS = json.loads(generate.ANSWERS.read_text(encoding="utf-8"))
SESSION_ANSWERS = ANSWERS["session"]["answers"]
START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
FULL_SET = [(item["kind"], item["file"], item.get("trial_no"), item.get("sentence_id")) for item in ANSWERS["session"]["recordings"]]
# 캘리브레이션 없이, 평가 문장을 더 크게 읽은 뒤 회차
LATER_SET = [(k, "paused_speech_louder" if f == "paused_speech" else f, t, s) for k, f, t, s in FULL_SET if k != "calibration_glide"]
OLD_VERSION = "0.9.0"


@pytest.fixture(scope="module")
def wavs(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    assert generate.main(["--out", str(out)]) == 0
    return {path.stem: path.read_bytes() for path in out.glob("*.wav")}


@pytest.fixture
def add_done(db, db_engine, storage, wavs):
    """세션을 만들고 처리 작업으로 done까지 보낸다. old=True면 서버 값과 캘리브레이션을 이전 버전 것으로 바꿔 둔다."""

    def add(user_id=None, purpose=Purpose.training, day=0, files=FULL_SET, old=True, status=SessionStatus.done):
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
            storage.client.put_object(Bucket=storage.bucket, Key=key, Body=wavs[name])
            db.add(
                RecordingRow(
                    id=recording_id, session_id=session_id, kind=RecordingKind(kind), trial_no=trial_no,
                    sentence_id=sentence_id, s3_key=key, duration_s=1.0, sample_rate=48000, size_bytes=len(wavs[name]),
                    uploaded_at=START, retain_until=START + timedelta(days=365),
                )
            )
        db.commit()
        if status is SessionStatus.done:
            with Session(db_engine, expire_on_commit=False) as worker_db:
                assert claim_next(worker_db) == session_id
                assert process_session(worker_db, storage, session_id) is SessionStatus.done
        if old:
            make_old(db, user_id, session_id)
        return user_id, session_id

    return add


def make_old(db, user_id, session_id):
    """알고리즘 버전을 올리기 전 상태를 흉내 낸다: 이 세션의 서버 값과 사용자 캘리브레이션을 이전 버전 것으로 바꾼다."""
    db.execute(
        update(MeasurementRow)
        .where(MeasurementRow.session_id == session_id, MeasurementRow.source == MeasurementSource.server)
        .values(algorithm_version=OLD_VERSION)
    )
    db.expire_all()
    user = db.get(UserRow, user_id)
    if user.calibration:
        user.calibration = {**user.calibration, "algorithm_version": OLD_VERSION}
    db.commit()


def run(argv, db, storage):
    lines = []
    code = recompute.main(argv, db=db, storage=storage, out=lines.append)
    return code, "\n".join(lines)


def server_rows(db, session_id, version=ALGORITHM_VERSION) -> dict[str, MeasurementRow]:
    db.expire_all()
    rows = db.scalars(
        select(MeasurementRow).where(
            MeasurementRow.session_id == session_id,
            MeasurementRow.source == MeasurementSource.server,
            MeasurementRow.algorithm_version == version,
        )
    ).all()
    return {row.metric: row for row in rows}


def near(value: float, expected: dict) -> bool:
    return abs(value - expected["value"]) <= expected["tolerance"]


def test_preview_shows_targets_and_writes_nothing(add_done, db, storage):
    user_id, first = add_done()
    _, second = add_done(user_id, day=7, files=LATER_SET)

    code, text = run(["--all"], db, storage)

    assert code == 0
    assert f"알고리즘 버전 {ALGORITHM_VERSION}: 다시 계산할 세션 2개" in text
    assert text.index(str(first)) < text.index(str(second))  # 시작 시각이 이른 것부터
    assert f"서버 값 버전: {OLD_VERSION}" in text and "--yes" in text
    assert server_rows(db, first) == {} and server_rows(db, second) == {}
    assert db.get(UserRow, user_id).calibration["algorithm_version"] == OLD_VERSION


def test_session_times_are_shown_in_korean_time_whatever_the_database_time_zone(add_done, db, storage):
    _, session_id = add_done()
    db.execute(text("SET TIME ZONE 'UTC'"))  # 운영 DB 연결처럼 UTC로 돌려주게 한다
    try:
        code, output = run(["--all"], db, storage)
    finally:
        db.execute(text("RESET TIME ZONE"))
        db.commit()

    # 09:00 UTC에 시작한 세션이다.
    assert code == 0 and f"{session_id}  사용자 " in output and "  2026-11-20 18:00 KST  training  " in output


def test_yes_recomputes_in_order_and_keeps_the_old_values(add_done, db, storage):
    user_id, first = add_done()
    _, second = add_done(user_id, day=7, files=LATER_SET)
    old_first = {metric: row.value for metric, row in server_rows(db, first, OLD_VERSION).items()}

    code, text = run(["--all", "--yes"], db, storage)

    assert code == 0
    assert "다시 계산했습니다: 세션 2개" in text and "실패 0개" in text
    values = server_rows(db, first)
    assert set(values) == {m for m in SESSION_ANSWERS if not m.startswith("calibration")}
    for metric, row in values.items():
        assert near(row.value, SESSION_ANSWERS[metric]), (metric, row.value)
    # 이전 버전 값은 그대로 남는다.
    assert {metric: row.value for metric, row in server_rows(db, first, OLD_VERSION).items()} == old_first
    # 평균 음량 변화: 첫 회차가 기준(0 dB), 뒤 회차는 지금 버전의 첫 회차 값과 뺀다.
    assert values["mean_level_change"].value == 0.0
    later = server_rows(db, second)["mean_level_change"]
    assert later.details["reference_session_id"] == str(first)
    assert near(later.value, ANSWERS["files"]["paused_speech_louder"]["mean_level_change_from_paused_speech"])
    # 캘리브레이션도 지금 버전으로 다시 구해 저장한다. 뒤 회차는 그 값을 쓴다.
    calibration = db.get(UserRow, user_id).calibration
    assert calibration["algorithm_version"] == ALGORITHM_VERSION and calibration["session_id"] == str(first)
    assert near(calibration["f0_low_hz"], SESSION_ANSWERS["calibration_low_hz"])
    assert server_rows(db, second)["f0_median"].details["search_range_hz"][0] < 200
    # 세션 상태, 기준선 표시, 시도 횟수는 바뀌지 않는다.
    for session_id, baseline in ((first, True), (second, False)):
        session = db.get(SessionRow, session_id)
        assert (session.status, session.is_baseline, session.processing_attempts, session.error) == ("done", baseline, 1, None)


def test_sessions_of_the_current_version_are_skipped_unless_forced(add_done, db, storage):
    _, session_id = add_done(old=False)
    before = {row.id for row in server_rows(db, session_id).values()}

    code, text = run(["--all", "--yes"], db, storage)

    assert code == 0 and "다시 계산할 세션 0개" in text and f"건너뜀({recompute.CURRENT}): 1개" in text
    assert {row.id for row in server_rows(db, session_id).values()} == before

    code, text = run(["--all", "--yes", "--force"], db, storage)

    assert code == 0 and "다시 계산했습니다: 세션 1개" in text
    after = server_rows(db, session_id)
    assert len(after) == len(before) and not {row.id for row in after.values()} & before


def test_later_session_alone_waits_for_the_earlier_one(add_done, db, storage):
    user_id, first = add_done()
    _, second = add_done(user_id, day=7, files=LATER_SET)

    code, text = run(["--session", str(second), "--yes"], db, storage)

    assert code == 1
    assert f"건너뜀({recompute.BLOCKED}): 1개" in text and "--user" in text
    assert server_rows(db, second) == {}

    assert run(["--session", str(first), "--yes"], db, storage)[0] == 0
    code, text = run(["--session", str(second), "--yes"], db, storage)

    assert code == 0 and "다시 계산했습니다: 세션 1개" in text
    assert server_rows(db, second)["mean_level_change"].details["reference_session_id"] == str(first)


def test_user_option_recomputes_only_that_user_and_other_purposes_do_not_block(add_done, db, storage):
    user_id, training = add_done()
    _, retest = add_done(user_id, purpose=Purpose.retest, day=3, files=LATER_SET)
    _, other = add_done()

    # 목적이 다른 앞선 세션은 기준 회차가 아니므로 막지 않는다.
    code, _ = run(["--session", str(retest), "--yes"], db, storage)

    assert code == 0
    assert server_rows(db, retest)["mean_level_change"].value == 0.0
    assert server_rows(db, training) == {}

    code, text = run(["--user", str(user_id), "--yes"], db, storage)

    assert code == 0 and "다시 계산했습니다: 세션 1개" in text
    assert server_rows(db, training) != {} and server_rows(db, other) == {}


def test_unreadable_recording_leaves_that_session_alone_and_goes_on(add_done, db, storage):
    _, broken = add_done()
    _, fine = add_done(day=1)
    key = db.scalar(select(RecordingRow.s3_key).where(RecordingRow.session_id == broken, RecordingRow.kind == RecordingKind.eval_sentence))
    storage.client.delete_object(Bucket=storage.bucket, Key=key)
    old = len(server_rows(db, broken, OLD_VERSION))

    code, text = run(["--all", "--yes"], db, storage)

    assert code == 1
    assert "다시 계산했습니다: 세션 1개" in text and "실패 1개" in text
    assert f"세션 {broken}: 실패(" in text
    assert server_rows(db, broken) == {} and len(server_rows(db, broken, OLD_VERSION)) == old
    assert db.get(SessionRow, broken).status == "done"
    assert server_rows(db, fine) != {}


def test_sessions_without_server_values_or_not_done_are_skipped(add_done, db, storage):
    agc = [("agc_tone", "steady_phonation", None, None)]
    _, agc_session = add_done(purpose=Purpose.agc_test, files=agc, old=False)
    _, waiting = add_done(old=False, status=SessionStatus.processing)

    code, text = run(["--all", "--yes"], db, storage)

    assert code == 0 and "다시 계산할 세션 0개" in text
    assert f"건너뜀({recompute.EMPTY}): 1개" in text and f"건너뜀({recompute.NOT_DONE}): 1개" in text

    # --force여도 done이 아닌 세션은 처리 작업의 몫이라 건드리지 않는다.
    code, text = run(["--all", "--yes", "--force"], db, storage)

    assert code == 0 and f"세션 {agc_session}: 지표 0개" in text
    assert str(waiting) not in text.split("건너뜀")[0]
    assert db.get(SessionRow, waiting).status == "processing" and server_rows(db, waiting) == {}


def test_session_that_stops_being_done_while_computing_is_not_written(add_done, db, db_engine, storage, monkeypatch):
    _, session_id = add_done()
    original = recompute.calculate

    def calculate(calc_db, calc_storage, session):
        result = original(calc_db, calc_storage, session)
        with Session(db_engine) as other:
            other.execute(update(SessionRow).where(SessionRow.id == session_id).values(status=SessionStatus.processing))
            other.commit()
        return result

    monkeypatch.setattr(recompute, "calculate", calculate)

    code, text = run(["--all", "--yes"], db, storage)

    assert code == 1 and f"쓰지 않음({recompute.CHANGED})" in text and "쓰지 않음 1개" in text
    assert server_rows(db, session_id) == {}


def test_unknown_session_or_user_is_an_input_error(add_done, db, storage, capsys):
    add_done()

    assert run(["--session", str(uuid.uuid4())], db, storage)[0] == 2
    assert run(["--user", str(uuid.uuid4())], db, storage)[0] == 2
    assert "찾지 못했습니다" in capsys.readouterr().err


def test_plan_blocks_every_later_candidate_behind_a_stale_session_outside_the_selection():
    user = uuid.uuid4()

    def item(day, purpose=Purpose.training, versions=(OLD_VERSION,), status=SessionStatus.done):
        return recompute.Item(uuid.uuid4(), user, purpose, START + timedelta(days=day), status, versions)

    earliest, middle, last = item(0), item(1), item(2)
    retest = item(3, Purpose.retest)
    current = item(4, versions=(OLD_VERSION, ALGORITHM_VERSION))
    selected = {middle.session_id, last.session_id, retest.session_id, current.session_id}

    plan = recompute.make_plan([earliest, middle, last, retest, current], lambda i: i.session_id in selected, force=False)

    assert plan.targets == [retest]
    assert plan.skipped == {recompute.CURRENT: [current], recompute.BLOCKED: [middle, last]}
    assert current.state == recompute.CURRENT and item(0, versions=()).state == recompute.EMPTY
    assert item(0, status=SessionStatus.failed).state == recompute.NOT_DONE
