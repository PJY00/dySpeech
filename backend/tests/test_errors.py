import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_error_handlers

SESSION_ID = str(uuid.uuid4())


def noise_floor() -> dict:
    return {"recording_id": str(uuid.uuid4()), "kind": "noise_floor", "duration_s": 2.0, "sample_rate": 48000, "size_bytes": 192_044}


def error_paths(response) -> list[str]:
    return [field["path"] for field in response.json()["error"]["fields"]]


def test_422_path_reaches_the_field_named_by_kind_rule(api_client):
    mpt_without_trial = noise_floor() | {"kind": "mpt_trial"}

    response = api_client.post(f"/v1/sessions/{SESSION_ID}/upload-urls", json={"recordings": [noise_floor(), mpt_without_trial]})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "요청이 계약과 다릅니다",
            "fields": [{"path": "body.recordings[1].trial_no", "reason": "mpt_trial 녹음에는 trial_no 값이 필요합니다"}],
        }
    }


def test_422_path_reaches_unit_field(api_client):
    body = {"ended_at": "2026-11-20T09:40:00Z", "device_measurements": [{"metric": "f0_median", "value": 180.0, "unit": "semitones"}]}

    response = api_client.post(f"/v1/sessions/{SESSION_ID}/complete", json=body)

    assert response.status_code == 422
    assert error_paths(response) == ["body.device_measurements[0].unit"]


def test_422_lists_every_problem_field(api_client):
    body = {
        "user_id": str(uuid.uuid4()),
        "started_at": "2026-11-20T09:15:00Z",
        "device_model": "SM-S921N",
        "os_version": "Android 16",
        "app_version": "0.1.0",
        "input_device": "wired_headset",
        "sample_rate": 22050,
        "note": "계약에 없는 필드",
    }

    response = api_client.put(f"/v1/sessions/{SESSION_ID}", json=body)

    assert response.status_code == 422
    assert sorted(error_paths(response)) == ["body.note", "body.purpose", "body.sample_rate"]


def test_422_for_path_id_and_missing_body(api_client):
    assert error_paths(api_client.get("/v1/sessions/not-a-uuid")) == ["path.session_id"]
    assert error_paths(api_client.put(f"/v1/users/{uuid.uuid4()}")) == ["body"]


def test_unknown_path_and_method_use_error_format(api_client):
    not_found = api_client.get("/v1/unknown")
    not_allowed = api_client.delete("/v1/metrics")

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "NOT_FOUND"
    assert not_allowed.status_code == 405
    assert not_allowed.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_unexpected_error_hides_details():
    broken_app = FastAPI()
    register_error_handlers(broken_app)

    @broken_app.get("/broken")
    def broken() -> None:
        raise RuntimeError("내부 정보")

    response = TestClient(broken_app, raise_server_exceptions=False).get("/broken")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR", "message": "서버에서 처리하지 못했습니다"}}
    assert "내부 정보" not in response.text
