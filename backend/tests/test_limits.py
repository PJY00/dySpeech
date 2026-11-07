"""요청 본문 크기 상한(1 MB, 413)을 확인한다."""

import asyncio
import json
import uuid

import pytest

from app.api.limits import MAX_BODY_BYTES, BodySizeLimitMiddleware

COMPLETE_PATH = f"/v1/sessions/{uuid.uuid4()}/complete"
TOO_LARGE = {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "요청 본문이 1 MB를 넘습니다"}}
# 상한 안의 본문은 처리기까지 간다. 등록하지 않은 세션이라 404가 나오면 본문을 끝까지 읽고 검증을 통과한 것이다.
REACHED_HANDLER = 404


def complete_body(size: int) -> bytes:
    """정확히 size바이트인 complete 요청 본문. client_meta의 채움 문자열로 길이를 맞춘다."""
    base = json.dumps({"ended_at": "2026-11-20T09:40:00Z", "client_meta": {"pad": ""}}, separators=(",", ":")).encode()
    body = base.replace(b'"pad":""', b'"pad":"' + b"a" * (size - len(base)) + b'"')
    assert len(body) == size
    return body


def post(client, body: bytes, **headers):
    return client.post(COMPLETE_PATH, content=body, headers={"Content-Type": "application/json", **headers})


def chunks(body: bytes, size: int = 64 * 1024):
    for start in range(0, len(body), size):
        yield body[start : start + size]


def test_body_at_the_limit_is_accepted(flow_client):
    response = post(flow_client, complete_body(MAX_BODY_BYTES))

    assert response.status_code == REACHED_HANDLER
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_body_over_the_limit_is_413(api_client):
    response = post(api_client, complete_body(MAX_BODY_BYTES + 1))

    assert response.status_code == 413
    assert response.json() == TOO_LARGE


@pytest.mark.parametrize(("size", "status"), [(MAX_BODY_BYTES, REACHED_HANDLER), (MAX_BODY_BYTES + 1, 413)])
def test_streamed_body_without_length_is_counted(flow_client, size, status):
    # 길이를 알리지 않고 나눠 보내는 요청도 읽으면서 센다.
    response = flow_client.post(COMPLETE_PATH, content=chunks(complete_body(size)), headers={"Content-Type": "application/json"})

    assert response.status_code == status


def test_too_large_is_checked_before_app_key(client_factory):
    client = client_factory("server-key", None)

    assert post(client, complete_body(MAX_BODY_BYTES + 1)).status_code == 413
    assert post(client, complete_body(1000)).status_code == 401


def test_requests_without_body_are_untouched(flow_client):
    assert flow_client.get("/v1/metrics").status_code == 200
    assert flow_client.get("/health").json() == {"status": "ok"}


def test_openapi_documents_413_for_every_v1_operation(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    v1_operations = [operation for path, operations in paths.items() if path.startswith("/v1") for operation in operations.values()]

    assert len(v1_operations) == 8
    assert all("413" in operation["responses"] for operation in v1_operations)


def run_middleware(messages: list[dict], max_bytes: int = 10) -> tuple[int, list[dict] | None]:
    """ASGI 메시지를 직접 넣어 미들웨어를 돌린다. (응답 상태, 앱이 받은 메시지)를 돌려준다."""
    received: list[dict] = []
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True
        while True:
            message = await receive()
            received.append(message)
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = list(messages)
    sent: list[dict] = []

    async def receive():
        return queue.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
    asyncio.run(BodySizeLimitMiddleware(app, max_bytes)(scope, receive, send))
    return sent[0]["status"], received if called else None


def body_messages(*parts: bytes) -> list[dict]:
    return [{"type": "http.request", "body": part, "more_body": i < len(parts) - 1} for i, part in enumerate(parts)]


def test_counts_across_several_messages():
    status, received = run_middleware(body_messages(b"1234", b"5678", b"90"))

    assert status == 200
    assert received == [{"type": "http.request", "body": b"1234567890", "more_body": False}]


def test_stops_reading_as_soon_as_the_limit_is_passed():
    status, received = run_middleware(body_messages(b"1234", b"5678", b"901", b"never read"))

    assert status == 413
    assert received is None


def test_disconnect_is_passed_to_the_app():
    status, received = run_middleware([{"type": "http.request", "body": b"12", "more_body": True}, {"type": "http.disconnect"}])

    assert status == 200
    assert received == [{"type": "http.disconnect"}]
