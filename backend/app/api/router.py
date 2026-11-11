from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from app.api import metrics, recordings, sessions, users
from app.api.auth import require_app_key
from app.api.errors import error_doc, register_error_handlers
from app.api.limits import BodySizeLimitMiddleware
from app.jobs.worker import ProcessingWorker, default_worker
from app.log_config import configure_logging
from app.settings import Settings, get_settings, require_operating_settings

# OpenAPI 문서의 info.version. API 명세가 바뀌면 올리고 api-changelog에 적는다.
API_VERSION = "0.2.1"

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


WorkerFactory = Callable[[], ProcessingWorker | None]


def add_startup(app: FastAPI, settings: Callable[[], Settings] | None = None, worker: WorkerFactory = default_worker) -> None:
    """
    서버 시작 단계에 운영 변수 확인과 처리 작업 스레드 시작을 붙임
    입력: app (FastAPI 앱), settings (설정을 돌려주는 함수, 없으면 get_settings: 환경 변수, 없으면 backend/.env), worker (처리 작업을 만드는 함수, 기본 default_worker. None을 돌려주면 처리 작업을 띄우지 않음)
    출력: 없음 (app.router.lifespan_context를 바꿈)
    알고리즘
    1. 앱의 원래 lifespan을 보관하고, 그것을 감싼 lifespan으로 바꿈
    2. 서버가 시작할 때 lifespan이 운영 변수가 모두 있는지 보고(빠지면 이름만 알리고 시작하지 않음), 처리 작업 스레드를 띄움
    3. 테스트 클라이언트는 with 블록으로 만들 때만 이 단계를 거침. 그래서 테스트는 운영 변수 없이 돌 수 있음
    """
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        """
        서버 수명 동안 운영 변수 확인과 처리 작업 시작·멈춤을 원래 lifespan 앞뒤로 실행함
        입력: app (FastAPI 앱)
        출력: 원래 lifespan이 넘기는 상태를 그대로 넘김(yield). app.state.processing_worker에 처리 작업(없으면 None)을 둠
        알고리즘
        1. 운영 변수가 모두 있는지 require_operating_settings로 확인함. 빠지면 이름만 알리는 RuntimeError로 시작하지 않음
        2. worker()로 처리 작업을 만들어 app.state.processing_worker에 두고, 있으면 스레드를 시작함
        3. 원래 lifespan 안에서 서버를 돌리고, 끝나면(오류로 끝나도) 처리 작업을 멈춤
        """
        require_operating_settings((settings or get_settings)())
        processing = worker()
        app.state.processing_worker = processing
        if processing is not None:
            processing.start()
        try:
            async with original(app) as state:
                yield state
        finally:
            if processing is not None:
                processing.stop()

    app.router.lifespan_context = lifespan


def register_api(
    app: FastAPI, settings: Callable[[], Settings] | None = None, worker: WorkerFactory = default_worker
) -> None:
    """
    앱에 로그 형식, OpenAPI 버전, 오류 형식, 요청 크기 상한, /v1 경로, 시작 단계를 붙임
    입력: app (FastAPI 앱), settings (설정을 돌려주는 함수, 없으면 get_settings), worker (처리 작업을 만드는 함수, 기본 default_worker)
    출력: 없음 (앱 설정을 바꿈)
    알고리즘
    1. 앱 로그 형식을 정함(configure_logging)
    2. OpenAPI 문서의 info.version을 API_VERSION으로 둠
    3. 오류 처리기를 붙이고(register_error_handlers), 요청 본문 1 MB 상한 미들웨어를 붙임
    4. /v1 라우터(모든 요청에 앱 키 확인)를 붙임
    5. 시작 단계(운영 변수 확인·처리 작업)를 붙임(add_startup)
    """
    configure_logging()
    app.version = API_VERSION
    register_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.include_router(router)
    add_startup(app, settings, worker)
