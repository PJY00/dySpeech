import pytest
from psycopg.conninfo import conninfo_to_dict

from app.settings import Settings

TEST_DATABASE_NAME = "dyspeech_test"


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
