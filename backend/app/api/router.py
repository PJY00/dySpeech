from fastapi import APIRouter, Depends, FastAPI

from app.api import metrics, recordings, sessions, users
from app.api.auth import require_app_key
from app.api.errors import error_doc, register_error_handlers
from app.api.limits import BodySizeLimitMiddleware
from app.log_config import configure_logging

# OpenAPI 문서의 info.version. API 명세가 바뀌면 올리고 api-changelog에 적는다.
API_VERSION = "0.1.0"

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


def register_api(app: FastAPI) -> None:
    """로그 형식, OpenAPI 버전, 오류 형식, 요청 크기 상한, /v1 경로를 앱에 붙인다."""
    configure_logging()
    app.version = API_VERSION
    register_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.include_router(router)
