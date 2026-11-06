from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import ErrorCode, error_response

MAX_BODY_BYTES = 1_048_576  # 1 MB
TOO_LARGE_MESSAGE = "요청 본문이 1 MB를 넘습니다"


class BodySizeLimitMiddleware:
    """요청 본문이 상한을 넘으면 앱에 넘기지 않고 413으로 답한다.

    Content-Length가 상한을 넘으면 본문을 읽지 않는다. 길이를 알리지 않은 요청은 읽으면서 세다가 넘는 순간 멈춘다.
    상한 안의 본문은 모두 읽어 둔 뒤 앱에 그대로 다시 넘긴다.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self.reject(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # 클라이언트가 끊었으면 그 메시지를 앱에 그대로 넘긴다.
                await self.app(scope, self.replay([message], receive), send)
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await self.reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        await self.app(scope, self.replay([{"type": "http.request", "body": b"".join(chunks), "more_body": False}], receive), send)

    @staticmethod
    def replay(messages: list[Message], receive: Receive) -> Receive:
        pending = list(messages)

        async def next_message() -> Message:
            return pending.pop(0) if pending else await receive()

        return next_message

    @staticmethod
    async def reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(413, ErrorCode.payload_too_large, TOO_LARGE_MESSAGE)
        await response(scope, receive, send)
