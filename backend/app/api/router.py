from fastapi import APIRouter, Depends, FastAPI

from app.api import metrics, recordings, sessions, users
from app.api.auth import require_app_key
from app.api.errors import error_doc, register_error_handlers

# OpenAPI 문서의 info.version. 계약이 바뀌면 올리고 api-changelog에 적는다.
API_VERSION = "0.1.0"

router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_app_key)],
    responses={401: error_doc("X-App-Key가 없거나 맞지 않음")},
)
for module in (users, sessions, recordings, metrics):
    router.include_router(module.router)


def register_api(app: FastAPI) -> None:
    """OpenAPI 버전, 오류 형식, /v1 경로를 앱에 붙인다."""
    app.version = API_VERSION
    register_error_handlers(app)
    app.include_router(router)
