"""진전도: 서버 값으로 만든 기준선·회차별 값·변화량과 판정 규칙을 확인한다(값은 DB에 직접 넣는다)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config.metrics import PROGRESS_METRICS
from app.db.models import MdcValueRow, MeasurementRow, MeasurementSource, SessionRow, UserRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import MdcScale, Purpose, SessionStatus
from app.schemas.history import History

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def user(db):
    user_id = uuid.uuid4()
    db.add(UserRow(id=user_id, created_at=START))
    db.commit()
    return user_id


@pytest.fixture
def add(db, user):
    def session(day: int, values: dict, purpose=Purpose.training, status=SessionStatus.done, version=ALGORITHM_VERSION, low=()):
        session_id = uuid.uuid4()
        db.add(
            SessionRow(
                id=session_id, user_id=user, purpose=purpose, started_at=START + timedelta(days=day), device_model="t",
                os_version="t", app_version="t", input_device="t", sample_rate=48000, status=status,
            )
        )
        db.flush()
        for metric, value in values.items():
            db.add(
                MeasurementRow(
                    session_id=session_id, metric=metric, value=value, unit="s", source=MeasurementSource.server,
                    algorithm_version=version, low_reliability=metric in low,
                )
            )
        db.commit()
        return session_id

    return session


def mdc(db, metric: str, value: float, scale=MdcScale.raw, active=True, version=ALGORITHM_VERSION):
    db.add(MdcValueRow(metric=metric, value=value, scale=scale, method="test", n_speakers=4, n_repeats=3, algorithm_version=version, active=active))
    db.commit()


def history(client, user_id) -> dict:
    response = client.get(f"/v1/users/{user_id}/history")
    assert response.status_code == 200, response.text
    History.model_validate(response.json())
    return {m["metric"]: m for m in response.json()["metrics"]}


def test_single_session_is_baseline_and_latest(flow_client, add, user):
    first = add(0, {"max_phonation_time": 12.0})

    mpt = history(flow_client, user)["max_phonation_time"]

    assert mpt["baseline"] == {"session_id": str(first), "value": 12.0}
    assert mpt["latest"] == {"value": 12.0, "change": 0.0}
    assert mpt["status"] == "pending_mdc"
    assert [p["session_id"] for p in mpt["series"]] == [str(first)]


@pytest.mark.parametrize(
    ("latest", "mdc_value", "status"),
    [(14.5, 2.0, "increase"), (9.5, 2.0, "decrease"), (13.5, 2.0, "no_change"), (12.0, 0.0, "no_change")],
)
def test_change_is_judged_against_mdc(flow_client, add, user, db, latest, mdc_value, status):
    add(0, {"max_phonation_time": 12.0})
    add(7, {"max_phonation_time": latest})
    mdc(db, "max_phonation_time", mdc_value)

    mpt = history(flow_client, user)["max_phonation_time"]

    assert mpt["latest"]["change"] == pytest.approx(latest - 12.0)
    assert mpt["mdc"] == {"value": mdc_value, "scale": "raw"}
    assert mpt["status"] == status


def test_log_scale_change_is_percent(flow_client, add, user, db):
    add(0, {"articulation_rate": 4.0})
    add(7, {"articulation_rate": 4.6})
    mdc(db, "articulation_rate", 12.0, MdcScale.log)

    rate = history(flow_client, user)["articulation_rate"]

    assert rate["latest"]["change"] == pytest.approx(15.0)
    assert rate["status"] == "increase"


def test_log_scale_with_zero_baseline_waits(flow_client, add, user, db):
    add(0, {"pause_ratio": 0.0})
    add(7, {"pause_ratio": 5.0})
    mdc(db, "pause_ratio", 10.0, MdcScale.log)

    pause = history(flow_client, user)["pause_ratio"]

    assert pause["latest"]["change"] == 5.0
    assert pause["status"] == "pending_mdc"


def test_low_reliability_latest_comes_before_mdc(flow_client, add, user, db):
    add(0, {"f0_sd_semitones": 2.0})
    add(7, {"f0_sd_semitones": 3.0}, low={"f0_sd_semitones"})
    mdc(db, "f0_sd_semitones", 0.3)

    assert history(flow_client, user)["f0_sd_semitones"]["status"] == "low_reliability"


def test_only_done_training_sessions_of_the_current_version_count(flow_client, add, user):
    first = add(0, {"speech_rate": 3.0})
    add(1, {"speech_rate": 9.0}, purpose=Purpose.retest)
    add(2, {"speech_rate": 9.0}, status=SessionStatus.processing)
    add(3, {"speech_rate": 9.0}, status=SessionStatus.failed)
    add(4, {"speech_rate": 9.0}, version="0.9.0")
    last = add(5, {"speech_rate": 3.5})

    rate = history(flow_client, user)["speech_rate"]

    assert [p["session_id"] for p in rate["series"]] == [str(first), str(last)]
    assert rate["latest"]["change"] == pytest.approx(0.5)


def test_baseline_is_the_first_session_with_that_value(flow_client, add, user):
    add(0, {"max_phonation_time": 10.0})
    second = add(7, {"max_phonation_time": 11.0, "pitch_range_semitones": 5.0})

    metrics = history(flow_client, user)

    assert metrics["pitch_range_semitones"]["baseline"]["session_id"] == str(second)
    assert metrics["f0_sd_semitones"]["status"] == "no_data"
    assert [m for m in metrics] == [m.value for m in PROGRESS_METRICS]


def test_mdc_of_another_version_or_inactive_is_ignored(flow_client, add, user, db):
    add(0, {"max_phonation_time": 12.0})
    mdc(db, "max_phonation_time", 2.0, version="0.9.0")
    mdc(db, "max_phonation_time", 3.0, active=False)

    assert history(flow_client, user)["max_phonation_time"]["mdc"] is None


def test_metric_definitions_show_active_mdc(flow_client, db):
    mdc(db, "max_phonation_time", 2.0)
    mdc(db, "articulation_rate", 12.0, MdcScale.log)
    mdc(db, "speech_rate", 1.0, version="0.9.0")

    definitions = {d["metric"]: d["mdc"] for d in flow_client.get("/v1/metrics").json()["metrics"]}

    assert definitions["max_phonation_time"] == {"value": 2.0, "scale": "raw"}
    assert definitions["articulation_rate"] == {"value": 12.0, "scale": "log"}
    assert definitions["speech_rate"] is None
