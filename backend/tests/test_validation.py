"""HTTP 요청으로 확인하는 입력 검증. 422 응답이 문제 필드의 경로를 정확히 가리키는지 본다."""

import uuid

import pytest

PATH_IDS = {"user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "recording_id": str(uuid.uuid4())}

ENDPOINTS = {
    "put_user": ("PUT", "/v1/users/{user_id}"),
    "get_history": ("GET", "/v1/users/{user_id}/history"),
    "put_session": ("PUT", "/v1/sessions/{session_id}"),
    "upload_urls": ("POST", "/v1/sessions/{session_id}/upload-urls"),
    "complete": ("POST", "/v1/sessions/{session_id}/complete"),
    "get_session": ("GET", "/v1/sessions/{session_id}"),
    "get_metrics": ("GET", "/v1/metrics"),
    "recording_url": ("GET", "/v1/recordings/{recording_id}/url"),
}

REMOVE = object()


def valid_bodies() -> dict[str, dict]:
    return {
        "put_user": {"created_at": "2026-11-20T09:15:00Z"},
        "put_session": {
            "user_id": PATH_IDS["user_id"],
            "purpose": "training",
            "started_at": "2026-11-20T09:15:00Z",
            "device_model": "SM-S921N",
            "os_version": "Android 16",
            "app_version": "0.1.0",
            "input_device": "wired_headset",
            "sample_rate": 48000,
        },
        "upload_urls": {
            "recordings": [
                {
                    "recording_id": str(uuid.uuid4()),
                    "kind": "noise_floor",
                    "duration_s": 2.0,
                    "sample_rate": 48000,
                    "size_bytes": 192_044,
                }
            ]
        },
        "complete": {
            "ended_at": "2026-11-20T09:40:00Z",
            "device_measurements": [{"metric": "speech_rate", "value": 3.1, "unit": "syllables/s"}],
            "game_runs": [
                {
                    "game_run_id": str(uuid.uuid4()),
                    "game": "breath_hold",
                    "level": 1,
                    "round_index": 1,
                    "started_at": "2026-11-20T09:20:00Z",
                    "success_rate_pct": 75.0,
                    "decision": "up",
                }
            ],
        },
    }


def edited(endpoint: str, path: list, value=REMOVE) -> dict:
    """올바른 본문에서 path 위치의 값 하나만 바꾸거나 지운다."""
    body = valid_bodies()[endpoint]
    target = body
    for key in path[:-1]:
        target = target[key]
    if value is REMOVE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return body


def body_path(path: list) -> str:
    return "body" + "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in path)


def send(client, endpoint: str, body: dict | None = None, **path_ids):
    method, template = ENDPOINTS[endpoint]
    if body is None:
        body = valid_bodies().get(endpoint)
    return client.request(method, template.format(**PATH_IDS | path_ids), json=body)


def error_paths(response) -> list[str]:
    assert response.status_code == 422, response.text
    return [field["path"] for field in response.json()["error"]["fields"]]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_valid_request_succeeds(api_client, endpoint):
    assert send(api_client, endpoint).status_code in (200, 202)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_endpoint_needs_app_key(client_factory, endpoint):
    response = send(client_factory("server-key", None), endpoint)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_APP_KEY"


PATH_ID_CASES = [
    (endpoint, name) for endpoint, (_, template) in ENDPOINTS.items() for name in PATH_IDS if f"{{{name}}}" in template
]


@pytest.mark.parametrize(("endpoint", "name"), PATH_ID_CASES)
@pytest.mark.parametrize("bad_id", ["not-a-uuid", str(uuid.uuid1())], ids=["not-uuid", "uuid1"])
def test_path_ids_must_be_uuid_v4(api_client, endpoint, name, bad_id):
    assert error_paths(send(api_client, endpoint, **{name: bad_id})) == [f"path.{name}"]


REQUIRED_FIELDS = [
    ("put_user", ["created_at"]),
    *[
        ("put_session", [field])
        for field in ("user_id", "purpose", "started_at", "device_model", "os_version", "app_version", "input_device", "sample_rate")
    ],
    ("upload_urls", ["recordings"]),
    *[("upload_urls", ["recordings", 0, field]) for field in ("recording_id", "kind", "duration_s", "sample_rate", "size_bytes")],
    ("complete", ["ended_at"]),
    *[("complete", ["device_measurements", 0, field]) for field in ("metric", "value", "unit")],
    *[
        ("complete", ["game_runs", 0, field])
        for field in ("game_run_id", "game", "level", "round_index", "started_at", "success_rate_pct", "decision")
    ],
]


@pytest.mark.parametrize(("endpoint", "path"), REQUIRED_FIELDS, ids=[body_path(p) for _, p in REQUIRED_FIELDS])
def test_missing_required_field_is_named(api_client, endpoint, path):
    assert error_paths(send(api_client, endpoint, edited(endpoint, path))) == [body_path(path)]


INVALID_VALUES = [
    ("put_user", ["created_at"], "yesterday"),
    ("put_user", ["created_at"], "2026-11-20T09:15:00"),
    ("put_session", ["user_id"], str(uuid.uuid1())),
    ("put_session", ["purpose"], "practice"),
    ("put_session", ["sample_rate"], 22050),
    ("put_session", ["device_model"], ""),
    ("put_session", ["client_meta"], ["목록은 받지 않음"]),
    ("upload_urls", ["recordings"], []),
    ("upload_urls", ["recordings", 0, "kind"], "unknown"),
    ("upload_urls", ["recordings", 0, "duration_s"], 121),
    ("upload_urls", ["recordings", 0, "size_bytes"], 0),
    ("complete", ["device_measurements", 0, "metric"], "jitter"),
    ("complete", ["device_measurements", 0, "unit"], "ms"),
    ("complete", ["device_measurements", 0, "value"], "fast"),
    ("complete", ["game_runs", 0, "decision"], "skip"),
    ("complete", ["game_runs", 0, "level"], 0),
    ("complete", ["game_runs", 0, "success_rate_pct"], 120),
    ("complete", ["game_runs", 0, "game"], "Breath Hold"),
]


@pytest.mark.parametrize(
    ("endpoint", "path", "value"), INVALID_VALUES, ids=[f"{body_path(p)}={v!r}" for _, p, v in INVALID_VALUES]
)
def test_invalid_value_is_named(api_client, endpoint, path, value):
    assert error_paths(send(api_client, endpoint, edited(endpoint, path, value))) == [body_path(path)]


KIND_RULES = [
    ("mpt_trial", {}, "trial_no"),
    ("eval_sentence", {}, "sentence_id"),
    ("game", {}, "game_run_id"),
    ("noise_floor", {"trial_no": 1}, "trial_no"),
    ("calibration_glide", {"sentence_id": "s1"}, "sentence_id"),
    ("agc_tone", {"game_run_id": str(uuid.uuid4())}, "game_run_id"),
]


@pytest.mark.parametrize(("kind", "extra", "field"), KIND_RULES, ids=[f"{k}-{f}" for k, _, f in KIND_RULES])
def test_kind_rule_names_the_field(api_client, kind, extra, field):
    body = valid_bodies()["upload_urls"]
    body["recordings"][0] |= {"kind": kind, **extra}

    response = send(api_client, "upload_urls", body)

    assert error_paths(response) == [f"body.recordings[0].{field}"]
    assert field in response.json()["error"]["fields"][0]["reason"]


UNKNOWN_FIELD_PARENTS = [
    ("put_user", []),
    ("put_session", []),
    ("upload_urls", []),
    ("upload_urls", ["recordings", 0]),
    ("complete", []),
    ("complete", ["device_measurements", 0]),
    ("complete", ["game_runs", 0]),
]


@pytest.mark.parametrize(("endpoint", "parent"), UNKNOWN_FIELD_PARENTS, ids=[body_path(p) for _, p in UNKNOWN_FIELD_PARENTS])
def test_unknown_field_is_rejected(api_client, endpoint, parent):
    body = edited(endpoint, [*parent, "note"], "계약에 없는 필드")

    assert error_paths(send(api_client, endpoint, body)) == [body_path([*parent, "note"])]


@pytest.mark.parametrize("endpoint", ["put_session", "complete"])
def test_client_meta_carries_extra_information(api_client, endpoint):
    body = edited(endpoint, ["client_meta"], {"screen": "measurement", "retry": 2, "nested": {"flags": [True, None]}})

    assert send(api_client, endpoint, body).status_code in (200, 202)


def test_old_success_rate_name_is_rejected(api_client):
    body = valid_bodies()["complete"]
    run = body["game_runs"][0]
    run["success_rate"] = run.pop("success_rate_pct") / 100

    paths = error_paths(send(api_client, "complete", body))

    assert sorted(paths) == ["body.game_runs[0].success_rate", "body.game_runs[0].success_rate_pct"]


@pytest.mark.parametrize(
    ("endpoint", "field"),
    [("upload_urls", "recordings"), ("complete", "device_measurements"), ("complete", "game_runs")],
)
def test_duplicates_point_to_the_list(api_client, endpoint, field):
    body = valid_bodies()[endpoint]
    body[field].append(dict(body[field][0]))

    response = send(api_client, endpoint, body)

    assert error_paths(response) == [f"body.{field}"]
    assert "겹칩니다" in response.json()["error"]["fields"][0]["reason"]


def test_broken_json_points_to_body(api_client):
    response = api_client.put(
        f"/v1/users/{PATH_IDS['user_id']}", content=b'{"created_at": ', headers={"Content-Type": "application/json"}
    )

    assert error_paths(response) == ["body"]
    assert response.json()["error"]["fields"][0]["reason"].startswith("JSON 형식이 아닙니다")
