from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """환경 변수에서 먼저 읽고, 없으면 backend/.env에서 읽는다.

    테스트와 도구가 일부 값만으로 돌 수 있도록 모든 값은 비어 있을 수 있다.
    서버는 시작할 때 운영 변수가 모두 있는지 확인한다(require_operating_settings).
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
    """
    설정에 빠진 운영 변수 이름을 모음
    입력: settings (서버 설정)
    출력: 값이 없는 운영 변수 이름 목록 (DATABASE_URL, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET, APP_API_KEY 중)
    알고리즘
    1. OPERATING_VARIABLES의 변수마다 설정 속성이 None이면 이름을 넣음
    """
    return [name for name, attribute in OPERATING_VARIABLES.items() if getattr(settings, attribute) is None]


def require_operating_settings(settings: Settings) -> None:
    """
    빠진 운영 변수가 있으면 이름만 알려주고 멈춤
    입력: settings (서버 설정)
    출력: 없음. 빠진 변수가 있으면 그 이름을 담은 RuntimeError를 일으킴(값은 출력하지 않음)
    알고리즘
    1. missing_operating_variables로 빠진 변수를 찾고, 있으면 "운영 변수가 없습니다: …. backend/.env나 배포 서버의 환경 변수에 넣으세요" 오류를 일으킴
    """
    missing = missing_operating_variables(settings)
    if missing:
        raise RuntimeError(f"운영 변수가 없습니다: {', '.join(missing)}. backend/.env나 배포 서버의 환경 변수에 넣으세요")


@lru_cache
def get_settings() -> Settings:
    """
    서버 설정을 한 번만 읽어 다시 씀
    입력: 없음 (환경 변수를 먼저 읽고, 없으면 backend/.env)
    출력: 서버 설정(Settings). 요청마다 .env를 다시 읽지 않도록 lru_cache로 하나만 둠. 테스트는 FastAPI 의존성 덮어쓰기로 바꿈
    알고리즘
    1. 처음 부를 때 Settings()를 만들고, 이후에는 같은 값을 돌려줌
    """
    return Settings()
