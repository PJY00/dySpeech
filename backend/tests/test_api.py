import uuid

import pytest

from app.config.metrics import METRICS, PROGRESS_METRICS
from app.schemas.history import History
from app.schemas.metrics import MetricDefinitions
from app.schemas.recordings import RecordingUrl, UploadUrlsOut
from app.schemas.sessions import SessionDetail

USER_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
RECORDING_IDS = [str(uuid.uuid4()), str(uuid.uuid4())]

SESSION_BODY = {
    "user_id": USER_ID,
    "purpose": "training",
    "started_at": "2026-11-20T09:15:00Z",
    "device_model": "SM-S921N",
    "os_version": "Android 16",
    "app_version": "0.1.0",
    "input_device": "wired_headset",
    "sample_rate": 48000,
    "client_meta": {"build": "debug"},
}
UPLOAD_URLS_BODY = {
    "recordings": [
        {"recording_id": RECORDING_IDS[0], "kind": "noise_floor", "duration_s": 2.0, "sample_rate": 48000, "size_bytes": 192_044},
        {
            "recording_id": RECORDING_IDS[1],
            "kind": "mpt_trial",
            "trial_no": 1,
            "duration_s": 15.5,
            "sample_rate": 48000,
            "size_bytes": 1_488_044,
        },
    ]
}
COMPLETE_BODY = {
    "ended_at": "2026-11-20T09:40:00Z",
    "device_measurements": [{"metric": "max_phonation_time", "value": 12.1, "unit": "s"}],
    "game_runs": [],
}


def test_put_user_returns_ids_and_time_in_utc(api_client):
    response = api_client.put(f"/v1/users/{USER_ID}", json={"created_at": "2026-11-20T18:15:00+09:00"})

    assert response.status_code == 200
    assert response.json() == {"id": USER_ID, "created_at": "2026-11-20T09:15:00Z"}


def test_put_session_returns_open_session(api_client):
    response = api_client.put(f"/v1/sessions/{SESSION_ID}", json=SESSION_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == SESSION_ID
    assert body["user_id"] == USER_ID
    assert body["status"] == "open"
    assert body["ended_at"] is None
    assert "client_meta" not in body


def test_upload_urls_gives_one_url_per_recording(api_client):
    response = api_client.post(f"/v1/sessions/{SESSION_ID}/upload-urls", json=UPLOAD_URLS_BODY)

    assert response.status_code == 200
    uploads = UploadUrlsOut.model_validate(response.json()).uploads
    assert [str(u.recording_id) for u in uploads] == RECORDING_IDS
    assert all(u.method == "PUT" and u.url.startswith("https://s3.example.com/") for u in uploads)


def test_complete_is_accepted(api_client):
    response = api_client.post(f"/v1/sessions/{SESSION_ID}/complete", json=COMPLETE_BODY)

    assert response.status_code == 202
    assert response.json() == {"session_id": SESSION_ID, "status": "processing"}


def test_get_session_returns_server_measurements(api_client):
    response = api_client.get(f"/v1/sessions/{SESSION_ID}")

    assert response.status_code == 200
    detail = SessionDetail.model_validate(response.json())
    assert str(detail.id) == SESSION_ID
    assert [m.metric for m in detail.server_measurements] == list(METRICS)


def test_history_lists_every_progress_metric_in_order(api_client):
    response = api_client.get(f"/v1/users/{USER_ID}/history")

    assert response.status_code == 200
    history = History.model_validate(response.json())
    assert [m.metric for m in history.metrics] == PROGRESS_METRICS
    assert {m.status for m in history.metrics} >= {"no_data", "increase", "decrease", "no_change", "pending_mdc"}


def test_metric_definitions_come_from_config(api_client):
    response = api_client.get("/v1/metrics")

    assert response.status_code == 200
    definitions = MetricDefinitions.model_validate(response.json()).metrics
    assert [(d.metric, d.unit, d.desired_direction) for d in definitions] == [
        (metric, spec.unit, spec.desired_direction) for metric, spec in METRICS.items()
    ]
    assert all(d.mdc is None for d in definitions)


def test_recording_url(api_client):
    response = api_client.get(f"/v1/recordings/{RECORDING_IDS[0]}/url")

    assert response.status_code == 200
    assert str(RecordingUrl.model_validate(response.json()).recording_id) == RECORDING_IDS[0]


@pytest.mark.parametrize(
    ("server_key", "request_key"),
    [("server-key", None), ("server-key", "wrong-key"), (None, "any-key"), (None, None)],
)
def test_v1_needs_matching_app_key(client_factory, server_key, request_key):
    client = client_factory(server_key, request_key)

    response = client.get("/v1/metrics")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_APP_KEY", "message": "X-App-Key가 없거나 맞지 않습니다"}}


def test_missing_key_is_rejected_before_body_validation(client_factory):
    client = client_factory("server-key", None)

    response = client.post(f"/v1/sessions/{SESSION_ID}/upload-urls", json={"recordings": []})

    assert response.status_code == 401


def test_health_needs_no_key(client_factory):
    client = client_factory(None, None)

    assert client.get("/health").json() == {"status": "ok"}


def test_openapi_documents_version_key_and_error_format(api_client):
    schema = api_client.get("/openapi.json").json()

    assert schema["info"]["version"] == "0.1.0"
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-App-Key"
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    v1_operations = [op for path, ops in schema["paths"].items() if path.startswith("/v1") for op in ops.values()]
    assert len(v1_operations) == 8
    for operation in v1_operations:
        assert "401" in operation["responses"]
        if operation.get("parameters") or operation.get("requestBody"):
            schema_ref = operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
            assert schema_ref.endswith("/ValidationErrorResponse")
