from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from app.api import metrics, recordings, sessions, users
from app.api.auth import require_app_key
from app.api.errors import error_doc, register_error_handlers
from app.api.limits import BodySizeLimitMiddleware
from app.log_config import configure_logging
from app.settings import Settings, get_settings, require_operating_settings

# OpenAPI 문서의 info.version. API 명세가 바뀌면 올리고 api-changelog에 적는다.
API_VERSION = "0.2.0"

router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_app_key)],
    responses={
        401: error_doc("X-App-Key가 없거나 맞지 않음"),
        413: error_doc("요청 본문이 1 MB를 넘음"),
    },
)
for module in (users, sessions, recordings, metrics):
    router.include_router(module.router)


def require_settings_on_startup(app: FastAPI, settings: Callable[[], Settings] | None = None) -> None:
    """서버가 시작할 때 운영 변수가 모두 있는지 본다. 빠지면 이름만 알리고 시작하지 않는다.

    테스트 클라이언트는 with 블록으로 만들 때만 이 단계를 거친다. 그래서 테스트는 운영 변수 없이 돈다.
    """
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        require_operating_settings((settings or get_settings)())
        async with original(app) as state:
            yield state

    app.router.lifespan_context = lifespan


def register_api(app: FastAPI, settings: Callable[[], Settings] | None = None) -> None:
    """로그 형식, OpenAPI 버전, 오류 형식, 요청 크기 상한, /v1 경로, 시작 때 운영 변수 확인을 앱에 붙인다."""
    configure_logging()
    app.version = API_VERSION
    register_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.include_router(router)
    require_settings_on_startup(app, settings)
