"""세션 점검 도구: 로컬 테스트 DB와 moto S3에 앱이 보낸 것 같은 세션을 넣고, API 명세와 어긋나는 점을 찾는지 확인한다."""

import io
import json
import uuid
import wave
from datetime import UTC, datetime, timedelta

import pytest

from app.config.metrics import METRICS
from app.db.models import GameRunRow, MeasurementRow, MeasurementSource, RecordingRow, SessionRow, UserRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Decision, Purpose, RecordingKind, SessionStatus
from app.storage.s3 import recording_key
from tools import inspect_session

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
NOW = START + timedelta(days=30)
RATE = 16000


def wav(seconds: float, rate: int = RATE, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(b"\x00\x00" * int(seconds * rate) * channels)
    return buffer.getvalue()


def rec(kind, seconds=3.0, **options) -> dict:
    return {"kind": kind, "seconds": seconds, **options}


# 첫 회차: 캘리브레이션 → 조용히 2초 → "아" 2회 → 평가 문장
FIRST = [
    rec("calibration_glide"),
    rec("noise_floor", 2.0),
    rec("mpt_trial", trial_no=1),
    rec("mpt_trial", trial_no=2),
    rec("eval_sentence", 4.0, sentence_id="s1"),
]
LATER = FIRST[1:]


def run_row(game="balloon", round_index=1, minutes=5, rate=100.0, run_id=None) -> dict:
    return {"id": run_id or uuid.uuid4(), "game": game, "round_index": round_index, "minutes": minutes, "rate": rate}


@pytest.fixture
def build(db, storage):
    """세션 하나와 녹음(S3 객체 포함), 기기 값, 서버 값, 게임 기록을 넣는다."""

    def add(
        files=FIRST, purpose=Purpose.training, user_id=None, day=0, status=SessionStatus.done, runs=(), device=True,
        server=tuple(METRICS), low=(), version=ALGORITHM_VERSION, ended_minutes=20, created_minutes=25, error=None,
        client_meta=None, session_rate=RATE,
    ):
        user_id = user_id or uuid.uuid4()
        if db.get(UserRow, user_id) is None:
            db.add(UserRow(id=user_id, created_at=START))
            db.flush()
        session_id = uuid.uuid4()
        started = START + timedelta(days=day)
        db.add(
            SessionRow(
                id=session_id, user_id=user_id, purpose=purpose, started_at=started,
                ended_at=started + timedelta(minutes=ended_minutes), device_model="SM-S921N", os_version="Android 16",
                app_version="1.0.0", input_device="wired_headset", sample_rate=session_rate, status=status, error=error,
                client_meta=client_meta or {}, created_at=started + timedelta(minutes=created_minutes),
            )
        )
        db.flush()
        ids = []
        for item in files:
            recording_id = uuid.uuid4()
            ids.append(recording_id)
            key = recording_key(user_id, session_id, recording_id)
            rate = item.get("rate", RATE)
            body = item.get("body", wav(item["seconds"], rate, item.get("channels", 1)))
            if item.get("upload", True):
                storage.client.put_object(Bucket=storage.bucket, Key=key, Body=body)
            db.add(
                RecordingRow(
                    id=recording_id, session_id=session_id, kind=RecordingKind(item["kind"]), trial_no=item.get("trial_no"),
                    sentence_id=item.get("sentence_id"), agc_signal=item.get("agc_signal"), game_run_id=item.get("game_run_id"),
                    s3_key=key, duration_s=item.get("declared_s", item["seconds"]), sample_rate=item.get("declared_rate", rate),
                    size_bytes=item.get("declared_size", len(body)), uploaded_at=started,
                    retain_until=started + timedelta(days=item.get("retain_days", 365)),
                )
            )
        for item in runs:
            db.add(
                GameRunRow(
                    id=item["id"], session_id=session_id, game=item["game"], level=1, round_index=item["round_index"],
                    started_at=started + timedelta(minutes=item["minutes"]), success_rate_pct=item["rate"], decision=Decision.hold,
                )
            )
        if device:
            db.add(MeasurementRow(session_id=session_id, metric="noise_floor", value=-60.0, unit="dBFS", source=MeasurementSource.device))
        for metric in server:
            db.add(
                MeasurementRow(
                    session_id=session_id, metric=metric.value, value=1.0, unit=METRICS[metric].unit.value,
                    source=MeasurementSource.server, algorithm_version=version, low_reliability=metric in low,
                )
            )
        db.commit()
        return user_id, session_id, ids

    return add


def run(argv, db, storage=None):
    lines = []
    code = inspect_session.main(argv, db=db, storage=storage, out=lines.append, now=NOW)
    return code, "\n".join(lines)


def findings(session_id, db, storage=None) -> dict[str, dict]:
    """세션 하나의 점검 결과를 {항목 이름: Finding}으로. storage가 없으면 파일은 보지 않는다."""
    argv = ["--session", str(session_id), "--json", *([] if storage else ["--no-s3"])]
    _, text = run(argv, db, storage)
    (report,) = json.loads(text)
    return {finding["code"]: finding for finding in report["findings"]}


def test_first_session_that_follows_the_specification_has_no_findings(build, db, storage):
    _, session_id, _ = build(runs=[run_row(round_index=1), run_row(round_index=2, minutes=8)])

    code, text = run(["--session", str(session_id)], db, storage)

    assert code == 0
    assert f"세션 {session_id} · training · 2026-11-20 18:00 KST · done" in text
    assert "기기 SM-S921N, OS Android 16, 앱 1.0.0, 입력 wired_headset" in text
    assert "'mpt_trial': 2" in text and "기기 지표 1개 · 서버 지표 11개 · 게임 기록 {'balloon': 2}" in text
    assert "문제 없음" in text
    assert "세션 1개 점검: 위반 0건(세션 0개), 주의 0건. 녹음 파일 점검함" in text


def test_measurement_block_counts_are_checked(build, db):
    files = [rec("noise_floor", 2.0), rec("noise_floor", 2.0), rec("mpt_trial", trial_no=2)]
    _, session_id, _ = build(files=files)

    found = findings(session_id, db)

    assert set(found) == {"NOISE_FLOOR_COUNT", "MPT_TRIAL_COUNT", "MPT_TRIAL_NO", "EVAL_SENTENCE_COUNT", "CALIBRATION_MISSING"}
    assert all(finding["level"] == "위반" for finding in found.values())
    assert "2개" in found["NOISE_FLOOR_COUNT"]["message"] and "[2]" in found["MPT_TRIAL_NO"]["message"]
    assert run(["--session", str(session_id), "--no-s3"], db)[0] == 1

    four = [*LATER, rec("mpt_trial", trial_no=3), rec("mpt_trial", trial_no=4), rec("calibration_glide")]
    assert set(findings(build(files=four)[1], db)) == {"MPT_TRIAL_COUNT"}


def test_calibration_belongs_to_the_first_measured_session(build, db):
    user_id, first, _ = build()
    _, later, ids = build(files=FIRST, user_id=user_id, day=1)
    _, plain, _ = build(files=LATER, user_id=user_id, day=2)
    _, twice, _ = build(files=[rec("calibration_glide"), *FIRST], user_id=user_id, day=3)

    assert findings(first, db) == {} and findings(plain, db) == {}
    found = findings(later, db)
    assert set(found) == {"CALIBRATION_LATER"}
    assert (found["CALIBRATION_LATER"]["level"], found["CALIBRATION_LATER"]["recording_id"]) == ("주의", str(ids[0]))
    assert set(findings(twice, db)) == {"CALIBRATION_COUNT"}
    # complete 전인 앞 세션은 첫 측정 세션으로 치지 않는다.
    other, _, _ = build(status=SessionStatus.open)
    assert findings(build(user_id=other, day=1)[1], db) == {}


def test_game_records_and_recordings_must_fit_the_purpose(build, db):
    linked = uuid.uuid4()
    files = [*FIRST, rec("game", game_run_id=linked), rec("game", game_run_id=uuid.uuid4()), rec("agc_tone", agc_signal="agc_hold")]
    _, training, ids = build(files=files, runs=[run_row(run_id=linked)])
    _, retest, _ = build(files=[*FIRST, rec("game", game_run_id=linked)], purpose=Purpose.retest, runs=[run_row()])

    found = findings(training, db)
    assert set(found) == {"GAME_RECORDING_UNLINKED", "KIND_NOT_ALLOWED"}
    assert found["GAME_RECORDING_UNLINKED"]["recording_id"] == str(ids[6])
    assert "agc_tone" in found["KIND_NOT_ALLOWED"]["message"]
    assert set(findings(retest, db)) == {"GAME_RUNS_NOT_ALLOWED", "KIND_NOT_ALLOWED", "GAME_RECORDING_UNLINKED"}


def test_agc_test_session_needs_each_tone_once_and_nothing_else(build, db):
    tones = [rec("agc_tone", agc_signal=signal) for signal in ("agc_steps", "agc_hold", "agc_toggle")]
    _, good, _ = build(files=tones, purpose=Purpose.agc_test, device=False, server=())
    wrong = [tones[0], tones[0], tones[1], rec("noise_floor", 2.0)]
    _, bad, _ = build(files=wrong, purpose=Purpose.agc_test, device=False, server=(), runs=[run_row()])

    assert findings(good, db) == {}
    found = findings(bad, db)
    assert set(found) == {"AGC_SIGNALS", "KIND_NOT_ALLOWED", "GAME_RUNS_NOT_ALLOWED"}
    assert "'agc_steps': 2" in found["AGC_SIGNALS"]["message"]


def test_recording_files_are_compared_with_what_the_app_declared(build, db, storage):
    files = [
        rec("calibration_glide", upload=False),
        rec("noise_floor", 8.0),
        rec("mpt_trial", trial_no=1, declared_s=10.0),
        rec("mpt_trial", trial_no=2, declared_size=10),
        rec("eval_sentence", 4.0, sentence_id="s1", channels=2),
        rec("game", 121.0, declared_s=120.0, rate=16000, game_run_id=uuid.uuid4()),
    ]
    game = files[5]["game_run_id"]
    _, session_id, ids = build(files=files, runs=[run_row(run_id=game)], session_rate=48000)

    _, text = run(["--session", str(session_id), "--json"], db, storage)
    found = [(finding["code"], finding["level"], finding["recording_id"]) for finding in json.loads(text)[0]["findings"]]

    assert set(found) == {
        ("OBJECT_MISSING", "위반", str(ids[0])),
        ("NOISE_FLOOR_LENGTH", "주의", str(ids[1])),
        ("DURATION_MISMATCH", "위반", str(ids[2])),
        ("SIZE_MISMATCH", "위반", str(ids[3])),
        ("WAV_FORMAT", "위반", str(ids[4])),
        ("DURATION_TOO_LONG", "위반", str(ids[5])),
        # 세션은 48000 Hz라고 알렸는데 녹음은 모두 16000 Hz다(규격이 틀린 파일과 없는 파일은 여기까지 오지 않는다).
        *{("SAMPLE_RATE_DIFFERS", "주의", str(ids[index])) for index in (1, 2, 3, 5)},
    }
    # --no-s3면 파일은 보지 않는다.
    code, text = run(["--session", str(session_id), "--no-s3"], db)
    assert code == 0 and "녹음 파일은 점검하지 않음(--no-s3)" in text and "문제 없음" in text


def test_unreadable_and_expired_files(build, db, storage):
    files = [rec("calibration_glide", body=b"not a wav file"), rec("noise_floor", 2.0, upload=False, retain_days=7), *LATER[1:]]
    _, session_id, ids = build(files=files)

    found = findings(session_id, db, storage)

    assert set(found) == {"WAV_FORMAT", "OBJECT_EXPIRED"}
    assert "WAV 파일이 아닙니다" in found["WAV_FORMAT"]["message"]
    assert (found["OBJECT_EXPIRED"]["level"], found["OBJECT_EXPIRED"]["recording_id"]) == ("주의", str(ids[1]))
    assert "2026-11-27" in found["OBJECT_EXPIRED"]["message"]


def test_device_values_and_game_records_are_reviewed(build, db):
    runs = [run_row(round_index=1, rate=0.8), run_row(round_index=3, rate=0.5, minutes=90), run_row("candle", 1, rate=1.0)]
    _, session_id, _ = build(runs=runs, device=False)
    _, whole, _ = build(runs=[run_row(round_index=1, rate=0.0), run_row(round_index=2, rate=1.0)])
    _, percent, _ = build(runs=[run_row(round_index=2, rate=80.0), run_row(round_index=1, rate=0.5)])

    found = findings(session_id, db)

    assert set(found) == {"NO_DEVICE_MEASUREMENTS", "ROUND_INDEX", "GAME_RUN_TIME", "SUCCESS_RATE_SCALE"}
    assert all(finding["level"] == "주의" for finding in found.values())
    assert "balloon" in found["ROUND_INDEX"]["message"] and "[1, 3]" in found["ROUND_INDEX"]["message"]
    assert "1개" in found["GAME_RUN_TIME"]["message"]
    # 0과 1뿐이거나 1을 넘는 값이 있으면 비율로 보낸 것으로 보지 않는다.
    assert findings(whole, db) == {} and findings(percent, db) == {}


def test_session_times_are_reviewed(build, db):
    _, backwards, _ = build(ended_minutes=-5)
    _, long, _ = build(ended_minutes=25 * 60, created_minutes=26 * 60)
    _, ahead, _ = build(created_minutes=-30)
    _, slightly, _ = build(created_minutes=-5)

    found = findings(backwards, db)
    assert found["ENDED_BEFORE_STARTED"]["level"] == "위반"
    assert set(findings(long, db)) == {"LONG_SESSION"}
    clock = findings(ahead, db)
    assert set(clock) == {"CLOCK"} and "30분" in clock["CLOCK"]["message"]
    assert findings(slightly, db) == {}


def test_server_outcome_is_reviewed(build, db):
    error = {"code": "PROCESSING_FAILED", "message": "서버가 녹음에서 지표를 계산하지 못했습니다"}
    _, failed, _ = build(status=SessionStatus.failed, server=(), error=error)
    present = tuple(metric for metric in METRICS if metric.value not in ("articulation_rate", "f0_median"))
    low = tuple(metric for metric in METRICS if metric.value == "max_phonation_time")
    _, partial, _ = build(server=present, low=low)
    _, old, _ = build(version="0.9.0")

    found = findings(failed, db)
    assert set(found) == {"FAILED"} and "PROCESSING_FAILED" in found["FAILED"]["message"]
    found = findings(partial, db)
    assert set(found) == {"MISSING_SERVER_METRICS", "LOW_RELIABILITY"}
    assert found["MISSING_SERVER_METRICS"]["message"].endswith("articulation_rate, f0_median")
    assert found["LOW_RELIABILITY"]["message"].endswith("max_phonation_time")
    # 다른 알고리즘 버전의 값은 지금 값으로 치지 않는다.
    assert set(findings(old, db)) == {"MISSING_SERVER_METRICS"}


def test_session_before_complete_is_only_noted(build, db, storage):
    _, session_id, _ = build(files=[rec("noise_floor", 2.0, upload=False)], status=SessionStatus.open, device=False, server=())

    found = findings(session_id, db, storage)

    assert set(found) == {"NOT_COMPLETED"} and found["NOT_COMPLETED"]["level"] == "주의"


def test_user_all_and_since_select_sessions_in_order(build, db):
    user_id, first, _ = build(client_meta={"dev": True})
    _, second, _ = build(files=[rec("noise_floor", 2.0)], user_id=user_id, day=2)
    _, other, _ = build(day=1)

    code, text = run(["--user", str(user_id), "--no-s3"], db)

    assert code == 1
    assert text.index(str(first)) < text.index(str(second)) and str(other) not in text
    assert f"세션 {first} · training · 2026-11-20 18:00 KST · done · 개발용" in text
    assert "[위반] MPT_TRIAL_COUNT: mpt_trial 녹음이 0개임. 세션마다 2~3개여야 함" in text
    assert "세션 2개 점검: 위반 2건(세션 1개), 주의 0건." in text

    # --since는 한국 시간 날짜다. 첫 세션은 한국 시간 11월 20일 18시에 시작했다.
    code, text = run(["--all", "--since", "2026-11-21", "--no-s3", "--json"], db)
    reports = json.loads(text)
    assert code == 1 and [report["session_id"] for report in reports] == [str(other), str(second)]
    assert reports[0]["started_at"] == "2026-11-21T18:00:00+09:00"
    assert len(json.loads(run(["--all", "--since", "2026-11-20", "--no-s3", "--json"], db)[1])) == 3
    assert reports[0]["recordings"] == {"calibration_glide": 1, "noise_floor": 1, "mpt_trial": 2, "eval_sentence": 1}
    assert (reports[0]["dev"], reports[0]["device_measurements"], reports[0]["server_measurements"]) == (False, 1, 11)


def test_no_matching_session_is_an_input_error(build, db, capsys):
    build()

    assert run(["--session", str(uuid.uuid4()), "--no-s3"], db)[0] == 2
    assert run(["--all", "--since", "2030-01-01", "--no-s3"], db)[0] == 2
    assert "점검할 세션을 찾지 못했습니다" in capsys.readouterr().err


def test_data_start_skips_other_chunks():
    plain = wav(1.0)
    assert inspect_session.data_start(plain[:64]) == 44
    # fmt 뒤에 홀수 길이 청크가 끼어 있어도 data를 찾는다.
    extra = plain[:36] + b"LIST" + (3).to_bytes(4, "little") + b"abc\x00" + plain[36:]
    assert inspect_session.data_start(extra[:128]) == 56
    assert inspect_session.data_start(plain[:40]) is None
