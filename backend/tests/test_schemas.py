import uuid

import pytest
from pydantic import ValidationError

from app.config.metrics import METRICS, PROGRESS_METRICS
from app.schemas.common import DesiredDirection, Metric
from app.schemas.history import History, MetricHistory
from app.schemas.recordings import RecordingIn, UploadUrlsIn
from app.schemas.sessions import CompleteIn, DeviceMeasurement, GameRun, SessionIn
from app.schemas.users import UserIn

GAME_RUN_ID = "6f1c2b7e-3d4a-4e5f-9a8b-1c2d3e4f5a6b"


def new_id() -> str:
    return str(uuid.uuid4())


def recording(**fields) -> dict:
    data = {
        "recording_id": new_id(),
        "kind": "noise_floor",
        "duration_s": 2.0,
        "sample_rate": 48000,
        "size_bytes": 192_044,
    }
    return data | fields


def session(**fields) -> dict:
    data = {
        "user_id": new_id(),
        "purpose": "training",
        "started_at": "2026-11-20T09:15:00Z",
        "device_model": "SM-S921N",
        "os_version": "Android 16",
        "app_version": "0.1.0",
        "input_device": "wired_headset",
        "sample_rate": 48000,
    }
    return data | fields


def game_run(**fields) -> dict:
    data = {
        "game_run_id": new_id(),
        "game": "breath_hold",
        "level": 1,
        "round_index": 1,
        "started_at": "2026-11-20T09:20:00Z",
        "success_rate_pct": 75.0,
        "decision": "up",
    }
    return data | fields


def metric_history(metric: Metric, **fields) -> dict:
    spec = METRICS[metric]
    data = {
        "metric": metric,
        "unit": spec.unit,
        "desired_direction": spec.desired_direction,
        "baseline": None,
        "series": [],
        "latest": None,
        "mdc": None,
        "status": "no_data",
    }
    return data | fields


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "calibration_glide"},
        {"kind": "noise_floor"},
        {"kind": "mpt_trial", "trial_no": 1},
        {"kind": "eval_sentence", "sentence_id": "s1"},
        {"kind": "game", "game_run_id": GAME_RUN_ID},
        {"kind": "agc_tone", "agc_signal": "agc_steps"},
    ],
)
def test_accepts_each_recording_kind(fields):
    RecordingIn(**recording(**fields))


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "mpt_trial"},
        {"kind": "eval_sentence"},
        {"kind": "game"},
        {"kind": "noise_floor", "trial_no": 1},
        {"kind": "agc_tone"},
        {"kind": "agc_tone", "agc_signal": "agc_unknown"},
        {"kind": "noise_floor", "agc_signal": "agc_steps"},
        {"kind": "mpt_trial", "trial_no": 1, "sentence_id": "s1"},
        {"kind": "mpt_trial", "trial_no": 0},
        {"kind": "unknown"},
        {"sample_rate": 22050},
        {"duration_s": 0},
        {"duration_s": 120.5},
        {"size_bytes": 0},
        {"recording_id": str(uuid.uuid1())},
        {"note": "API 명세에 없는 필드"},
    ],
)
def test_rejects_invalid_recording(fields):
    with pytest.raises(ValidationError):
        RecordingIn(**recording(**fields))


def test_kind_field_error_names_the_field():
    with pytest.raises(ValidationError) as caught:
        UploadUrlsIn(recordings=[recording(), recording(kind="mpt_trial")])

    error = caught.value.errors()[0]
    assert error["loc"] == ("recordings", 1)
    assert error["ctx"]["field"] == "trial_no"
    assert error["msg"] == "mpt_trial 녹음에는 trial_no 값이 필요합니다"


def test_upload_urls_needs_unique_recordings():
    duplicated = recording()
    with pytest.raises(ValidationError, match="겹칩니다"):
        UploadUrlsIn(recordings=[duplicated, duplicated])
    with pytest.raises(ValidationError):
        UploadUrlsIn(recordings=[])


def test_times_need_timezone_and_are_returned_in_utc():
    with pytest.raises(ValidationError):
        UserIn(created_at="2026-11-20T09:15:00")

    user = UserIn(created_at="2026-11-20T18:15:00+09:00")

    assert user.model_dump_json() == '{"created_at":"2026-11-20T09:15:00Z"}'


@pytest.mark.parametrize(
    "fields",
    [
        {"purpose": "practice"},
        {"sample_rate": 22050},
        {"device_model": ""},
        {"client_meta": ["목록은 받지 않음"]},
        {"client_meta": "문자열은 받지 않음"},
    ],
)
def test_rejects_invalid_session(fields):
    with pytest.raises(ValidationError):
        SessionIn(**session(**fields))


def test_client_meta_takes_any_json_object():
    meta = {"screen": "measurement", "retry": 2, "flags": [True, None], "nested": {"rms_db": -23.5}}

    assert SessionIn(**session()).client_meta == {}
    assert SessionIn(**session(client_meta=meta)).client_meta == meta
    assert CompleteIn(ended_at="2026-11-20T09:40:00Z", client_meta=meta).client_meta == meta


def test_device_measurement_unit_must_match_metric():
    DeviceMeasurement(metric="max_phonation_time", value=12.3, unit="s")

    with pytest.raises(ValidationError, match="단위"):
        DeviceMeasurement(metric="max_phonation_time", value=12.3, unit="dB")


@pytest.mark.parametrize(("metric", "unit"), [("f0_median", "Hz"), ("noise_floor", "dBFS"), ("sustained_level", "dBFS")])
def test_accepts_auxiliary_device_measurements(metric, unit):
    DeviceMeasurement(metric=metric, value=-50.0, unit=unit)


@pytest.mark.parametrize(
    "fields",
    [
        {"metric": "jitter", "unit": "%"},
        {"metric": "f0_median", "unit": "semitones"},
        {"value": float("nan")},
        {"value": float("inf")},
    ],
)
def test_rejects_invalid_device_measurement(fields):
    data = {"metric": "max_phonation_time", "value": 12.3, "unit": "s"} | fields
    with pytest.raises(ValidationError):
        DeviceMeasurement(**data)


@pytest.mark.parametrize(
    "fields",
    [
        {"success_rate_pct": 100.5},
        {"success_rate_pct": -1},
        {"level": 0},
        {"round_index": 0},
        {"game": "Breath Hold"},
        {"decision": "skip"},
    ],
)
def test_rejects_invalid_game_run(fields):
    with pytest.raises(ValidationError):
        GameRun(**game_run(**fields))


def test_old_success_rate_name_is_rejected():
    data = game_run()
    del data["success_rate_pct"]

    with pytest.raises(ValidationError):
        GameRun(**data | {"success_rate": 0.8})


def test_level_has_no_upper_limit():
    assert GameRun(**game_run(level=1000)).level == 1000


def test_complete_needs_unique_metrics_and_game_runs():
    measurement = {"metric": "speech_rate", "value": 3.1, "unit": "syllables/s"}
    run = game_run()

    CompleteIn(ended_at="2026-11-20T09:40:00Z", device_measurements=[measurement], game_runs=[run])
    with pytest.raises(ValidationError, match="metric"):
        CompleteIn(ended_at="2026-11-20T09:40:00Z", device_measurements=[measurement, measurement])
    with pytest.raises(ValidationError, match="game_run_id"):
        CompleteIn(ended_at="2026-11-20T09:40:00Z", game_runs=[run, run])


def test_complete_without_games_is_allowed():
    complete = CompleteIn(ended_at="2026-11-20T09:40:00Z")

    assert complete.device_measurements == []
    assert complete.game_runs == []


def test_metric_definitions_cover_every_metric():
    assert set(METRICS) == set(Metric)


def test_speech_rate_metrics_have_no_desired_direction():
    for metric in (Metric.articulation_rate, Metric.speech_rate, Metric.pause_ratio):
        assert METRICS[metric].desired_direction is DesiredDirection.none


def test_auxiliary_metrics_are_not_used_for_progress():
    auxiliary = {Metric.sustained_level, Metric.f0_median, Metric.noise_floor}

    assert len(PROGRESS_METRICS) == 8
    assert auxiliary.isdisjoint(PROGRESS_METRICS)
    for metric in auxiliary:
        assert METRICS[metric].desired_direction is DesiredDirection.none


def test_history_lists_every_progress_metric_even_without_data():
    history = History(
        user_id=new_id(),
        metrics=[metric_history(m) for m in PROGRESS_METRICS],
        recent_device_measurements=[],
    )

    assert [m.status for m in history.metrics] == ["no_data"] * 8


@pytest.mark.parametrize(
    "metrics",
    [
        [metric_history(m) for m in PROGRESS_METRICS[:-1]],
        [metric_history(m) for m in reversed(PROGRESS_METRICS)],
        [metric_history(m) for m in PROGRESS_METRICS] + [metric_history(Metric.sustained_level)],
    ],
)
def test_history_rejects_missing_reordered_or_auxiliary_metrics(metrics):
    with pytest.raises(ValidationError, match="순서"):
        History(user_id=new_id(), metrics=metrics, recent_device_measurements=[])


def test_metric_history_with_values():
    session_id = new_id()
    filled = {
        "baseline": {"session_id": session_id, "value": 10.0},
        "series": [{"session_id": session_id, "started_at": "2026-11-20T09:15:00Z", "value": 10.0, "low_reliability": False}],
        "latest": {"value": 10.0, "change": 0.0},
        "status": "pending_mdc",
    }

    MetricHistory(**metric_history(Metric.max_phonation_time, **filled))
    with pytest.raises(ValidationError, match="no_data"):
        MetricHistory(**metric_history(Metric.max_phonation_time, **filled | {"status": "no_data"}))
    with pytest.raises(ValidationError, match="모두"):
        MetricHistory(**metric_history(Metric.max_phonation_time, **filled | {"latest": None}))
    with pytest.raises(ValidationError, match="no_data"):
        MetricHistory(**metric_history(Metric.max_phonation_time, status="pending_mdc"))
