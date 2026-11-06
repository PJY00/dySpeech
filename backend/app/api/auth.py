import secrets
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.api.errors import ApiError, ErrorCode
from app.settings import Settings, get_settings

app_key_header = APIKeyHeader(name="X-App-Key", auto_error=False, description="앱 요청을 확인하는 키")


def require_app_key(
    app_key: Annotated[str | None, Security(app_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    # 서버에 APP_API_KEY가 없으면 모든 /v1 요청을 거부한다. 응답에서는 어느 쪽 문제인지 밝히지 않는다.
    expected = settings.app_api_key
    if (
        expected is None
        or app_key is None
        or not secrets.compare_digest(app_key.encode(), expected.get_secret_value().encode())
    ):
        raise ApiError(401, ErrorCode.invalid_app_key, "X-App-Key가 없거나 맞지 않습니다")
