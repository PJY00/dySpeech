"""서버 시작 때 운영 변수를 확인한다. 테스트 클라이언트를 with 블록으로 열면 시작 단계를 거친다."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router as api_router
from app.main import app as main_app
from app.settings import OPERATING_VARIABLES, Settings, get_settings

ALL_SET = {attribute: f"value-of-{name}" for name, attribute in OPERATING_VARIABLES.items()}


def make_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    api_router.register_api(app, settings=lambda: settings, worker=lambda: None)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def test_server_does_not_start_without_operating_variables():
    with pytest.raises(RuntimeError) as error, TestClient(make_app(Settings(_env_file=None, aws_region="ap-northeast-2"))):
        pass

    message = str(error.value)
    assert all(name in message for name in OPERATING_VARIABLES if name != "AWS_REGION")
    assert "AWS_REGION" not in message


def test_server_starts_when_every_operating_variable_is_set():
    with TestClient(make_app(Settings(_env_file=None, **ALL_SET))) as client:
        assert client.get("/openapi.json").status_code == 200


def test_missing_values_are_named_without_printing_values():
    settings = Settings(_env_file=None, **(ALL_SET | {"s3_bucket": None}))

    with pytest.raises(RuntimeError) as error, TestClient(make_app(settings)):
        pass

    assert "S3_BUCKET" in str(error.value)
    assert not any(value in str(error.value) for value in ALL_SET.values())


def test_main_app_checks_on_startup(monkeypatch):
    # 실제 앱(app.main)에도 연결되어 있는지 본다. .env를 읽지 않도록 설정 함수를 바꾼다.
    monkeypatch.setattr(api_router, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(RuntimeError, match="운영 변수가 없습니다"), TestClient(main_app):
        pass


def test_health_without_startup_needs_no_variables():
    # with 없이 만든 클라이언트(기존 /health 테스트 방식)는 시작 단계를 거치지 않는다.
    assert TestClient(main_app).get("/health").json() == {"status": "ok"}
