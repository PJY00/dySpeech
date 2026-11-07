import logging
import uuid

import pytest

from app.config.metrics import METRICS
from app.schemas.metrics import MetricDefinitions

USER_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


def test_metric_definitions_come_from_config(api_client):
    response = api_client.get("/v1/metrics")

    assert response.status_code == 200
    definitions = MetricDefinitions.model_validate(response.json()).metrics
    assert [(d.metric, d.unit, d.desired_direction) for d in definitions] == [
        (metric, spec.unit, spec.desired_direction) for metric, spec in METRICS.items()
    ]
    assert all(d.mdc is None for d in definitions)


@pytest.mark.parametrize(
    ("server_key", "request_key"),
    [("server-key", None), ("server-key", "wrong-key"), (None, "any-key"), (None, None)],
)
def test_v1_needs_matching_app_key(client_factory, server_key, request_key):
    client = client_factory(server_key, request_key)

    response = client.get("/v1/metrics")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_APP_KEY", "message": "X-App-Key가 없거나 맞지 않습니다"}}


@pytest.mark.parametrize(
    ("server_key", "request_key", "reason"),
    [
        (None, "request-key", "서버 키 없음"),
        ("server-key", None, "요청 키 없음"),
        ("server-key", "wrong-key", "키 불일치"),
    ],
)
def test_auth_failure_reason_is_logged_without_keys_or_ids(client_factory, caplog, server_key, request_key, reason):
    client = client_factory(server_key, request_key)

    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        client.get(f"/v1/users/{USER_ID}/history")

    messages = [record.getMessage() for record in caplog.records if record.name == "app.api.auth"]
    assert len(messages) == 1
    assert reason in messages[0]
    assert messages[0].endswith("GET /v1/users/{user_id}/history")
    for value in (server_key, request_key, USER_ID):
        if value:
            assert value not in caplog.text


def test_successful_request_logs_nothing(api_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        api_client.get("/v1/metrics")

    assert not [record for record in caplog.records if record.name == "app.api.auth"]


def test_missing_key_is_rejected_before_body_validation(client_factory):
    client = client_factory("server-key", None)

    response = client.post(f"/v1/sessions/{SESSION_ID}/upload-urls", json={"recordings": []})

    assert response.status_code == 401


def test_health_needs_no_key(client_factory):
    client = client_factory(None, None)

    assert client.get("/health").json() == {"status": "ok"}


def test_openapi_documents_version_key_and_error_format(api_client):
    schema = api_client.get("/openapi.json").json()

    assert schema["info"]["version"] == "0.2.0"
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-App-Key"
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    v1_operations = [op for path, ops in schema["paths"].items() if path.startswith("/v1") for op in ops.values()]
    assert len(v1_operations) == 8
    for operation in v1_operations:
        assert "401" in operation["responses"]
        if operation.get("parameters") or operation.get("requestBody"):
            schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
            refs = [item["$ref"] for item in schema.get("anyOf", [schema])]
            assert refs[0].endswith("/ValidationErrorResponse")


def test_openapi_documents_wav_format_422_on_complete(api_client):
    schema = api_client.get("/openapi.json").json()

    response = schema["paths"]["/v1/sessions/{session_id}/complete"]["post"]["responses"]["422"]
    refs = [item["$ref"].rsplit("/", 1)[-1] for item in response["content"]["application/json"]["schema"]["anyOf"]]
    assert refs == ["ValidationErrorResponse", "InvalidRecordingsResponse"]
    detail = schema["components"]["schemas"]["InvalidRecordingsDetail"]
    assert set(detail["required"]) == {"code", "message", "invalid_recordings"}
