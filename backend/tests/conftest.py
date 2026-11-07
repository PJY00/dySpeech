from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import make_engine
from app.main import app
from app.settings import Settings, get_settings
from app.storage.s3 import make_storage

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_NAME = "dyspeech_test"
TEST_APP_KEY = "test-app-key"
TEST_REGION = "ap-northeast-2"
TEST_BUCKET = "dyspeech-test-recordings"


def make_client(server_key: str | None, request_key: str | None) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_api_key=server_key)
    headers = {"X-App-Key": request_key} if request_key is not None else {}
    return TestClient(app, headers=headers)


@pytest.fixture
def api_client():
    """서버와 요청의 앱 키가 맞는 클라이언트."""
    yield make_client(TEST_APP_KEY, TEST_APP_KEY)
    app.dependency_overrides.clear()


@pytest.fixture
def client_factory():
    """서버 키와 요청 키를 골라 만드는 클라이언트."""
    yield make_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """로컬 테스트 DB 주소. DB를 쓰는 테스트는 모두 이 fixture를 거친다."""
    url = Settings().test_database_url
    if url is None:
        pytest.skip("TEST_DATABASE_URL이 없어 로컬 PostgreSQL 테스트를 건너뜁니다")

    # 테이블을 지우는 테스트가 다른 DB에 닿지 않도록, 이름이 다르면 테스트 전체를 멈춘다.
    name = conninfo_to_dict(url.get_secret_value()).get("dbname")
    if name != TEST_DATABASE_NAME:
        pytest.exit(
            f"TEST_DATABASE_URL의 DB 이름은 {TEST_DATABASE_NAME}여야 합니다(현재: {name}). 테스트를 중단합니다.",
            returncode=1,
        )
    return url.get_secret_value()


@pytest.fixture(scope="session")
def migration_config(postgres_url) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = postgres_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def db_engine(postgres_url, migration_config):
    """테스트 DB를 비우고 마이그레이션을 끝까지 적용한다. 테스트 실행마다 한 번 한다."""
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")
    engine = make_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    """테스트마다 새 DB 세션. 테스트가 끝나면 모든 테이블을 비운다."""
    with Session(db_engine, expire_on_commit=False) as session:
        yield session
    with db_engine.begin() as connection:
        connection.execute(text("TRUNCATE users, mdc_values RESTART IDENTITY CASCADE"))


@pytest.fixture
def storage():
    """moto로 흉내 낸 녹음 버킷. 실제 AWS에는 접속하지 않는다."""
    settings = Settings(
        _env_file=None,
        aws_region=TEST_REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        s3_bucket=TEST_BUCKET,
    )
    with mock_aws():
        storage = make_storage(settings)
        storage.client.create_bucket(Bucket=TEST_BUCKET, CreateBucketConfiguration={"LocationConstraint": TEST_REGION})
        yield storage
