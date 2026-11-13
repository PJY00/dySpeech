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
        """
        요청 본문 크기 상한 미들웨어를 초기화함
        입력: app (다음 ASGI 앱), max_bytes (본문 상한, 바이트. 기본 MAX_BODY_BYTES = 1,048,576바이트(1 MB))
        출력: 없음
        알고리즘
        1. 다음 앱과 상한을 저장함
        """
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        요청 본문이 상한을 넘으면 앱에 넘기지 않고 413으로 답하고, 상한 안이면 읽어 둔 본문을 앱에 다시 넘김
        입력: scope (ASGI 연결 정보), receive (요청 메시지를 받는 함수), send (응답 메시지를 보내는 함수)
        출력: 없음. 상한을 넘으면 413 PAYLOAD_TOO_LARGE 응답을 보냄
        알고리즘
        1. HTTP 요청이 아니면 그대로 앱에 넘김
        2. Content-Length 헤더가 숫자이고 상한을 넘으면 본문을 읽지 않고 413으로 답함
        3. 본문 조각을 받으며 크기를 더함. 길이를 알리지 않은 요청도 합이 상한을 넘는 순간 멈추고 413으로 답함
        4. 요청 메시지가 아닌 것(클라이언트 연결 끊김 등)이 오면 그 메시지를 앱에 그대로 넘김
        5. 끝까지 받았으면 조각을 하나로 이어, 한 번의 요청 메시지로 앱에 다시 넘김(replay)
        """
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
        """
        미리 받아 둔 메시지를 먼저 돌려주는 receive 함수를 만들어 돌려줌
        입력: messages (먼저 돌려줄 ASGI 메시지 목록), receive (그다음에 쓸 원래 receive 함수)
        출력: 새 receive 함수(next_message)
        알고리즘
        1. 메시지 목록을 복사해 두고, 그 목록을 앞에서부터 꺼내는 next_message를 돌려줌
        """
        pending = list(messages)

        async def next_message() -> Message:
            """
            미리 받은 메시지를 하나씩 꺼내고, 다 쓰면 원래 receive로 받음
            입력: 없음
            출력: ASGI 메시지
            알고리즘
            1. 목록에 남은 것이 있으면 맨 앞을 꺼내 돌려주고, 없으면 원래 receive()를 기다려 돌려줌
            """
            return pending.pop(0) if pending else await receive()

        return next_message

    @staticmethod
    async def reject(scope: Scope, receive: Receive, send: Send) -> None:
        """
        413 PAYLOAD_TOO_LARGE 응답을 보냄
        입력: scope (ASGI 연결 정보), receive (요청 메시지를 받는 함수), send (응답 메시지를 보내는 함수)
        출력: 없음 (상태 413, 메시지 "요청 본문이 1 MB를 넘습니다"인 응답을 보냄)
        알고리즘
        1. error_response로 413 응답을 만들어 보냄
        """
        response = error_response(413, ErrorCode.payload_too_large, TOO_LARGE_MESSAGE)
        await response(scope, receive, send)
