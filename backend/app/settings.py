from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """환경 변수에서 먼저 읽고, 없으면 backend/.env에서 읽는다.

    값이 없어도 서버가 뜨도록 모든 값은 비어 있을 수 있다.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # 운영 변수
    database_url: SecretStr | None = None
    aws_region: str | None = None
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    s3_bucket: str | None = None
    app_api_key: SecretStr | None = None

    # 개발 변수
    test_database_url: SecretStr | None = None


# 실제 저장을 하는 서버가 시작할 때 모두 있어야 하는 변수
OPERATING_VARIABLES = {
    "DATABASE_URL": "database_url",
    "AWS_REGION": "aws_region",
    "AWS_ACCESS_KEY_ID": "aws_access_key_id",
    "AWS_SECRET_ACCESS_KEY": "aws_secret_access_key",
    "S3_BUCKET": "s3_bucket",
    "APP_API_KEY": "app_api_key",
}


def missing_operating_variables(settings: Settings) -> list[str]:
    return [name for name, attribute in OPERATING_VARIABLES.items() if getattr(settings, attribute) is None]


def require_operating_settings(settings: Settings) -> None:
    """빠진 운영 변수가 있으면 이름만 알려주고 멈춘다(값은 출력하지 않는다)."""
    missing = missing_operating_variables(settings)
    if missing:
        raise RuntimeError(f"운영 변수가 없습니다: {', '.join(missing)}. backend/.env나 배포 서버의 환경 변수에 넣으세요")


@lru_cache
def get_settings() -> Settings:
    """요청마다 .env를 다시 읽지 않도록 한 번 읽은 값을 쓴다. 테스트는 FastAPI 의존성 덮어쓰기로 바꾼다."""
    return Settings()
