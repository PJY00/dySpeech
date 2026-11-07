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
