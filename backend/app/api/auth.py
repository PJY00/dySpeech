import logging
import secrets
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from app.api.errors import ApiError, ErrorCode
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

app_key_header = APIKeyHeader(name="X-App-Key", auto_error=False, description="앱 요청을 확인하는 키")


def require_app_key(
    request: Request,
    app_key: Annotated[str | None, Security(app_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """
    /v1 요청마다 X-App-Key가 서버 키와 같은지 확인하고, 다르면 401 오류를 냄
    입력: request (HTTP 요청, 실패 로그의 메서드·경로용), app_key (X-App-Key 헤더 값, 없으면 None), settings (서버 설정의 app_api_key, 환경 변수 APP_API_KEY, app/settings.py)
    출력: 없음. 키가 맞지 않으면 경고 로그를 남기고 ApiError(401, INVALID_APP_KEY)를 일으킴
    알고리즘
    1. 실패 이유를 정함: 서버 키 없음(APP_API_KEY가 설정되지 않음), 요청 키 없음(X-App-Key 헤더가 없음), 키 불일치(secrets.compare_digest로 비교). 셋 다 아니면 그대로 끝냄
    2. 로그에 ID 값이 남지 않도록 경로 안의 경로 매개변수 값을 {이름}으로 바꿈 (예: /v1/users/{user_id})
    3. 이유·메서드·경로를 경고 로그로 남기고(키 값은 남기지 않음) 401 INVALID_APP_KEY 오류를 일으킴. 응답에서는 어느 쪽 문제인지 밝히지 않고, 서버 설정 실수는 로그로 찾음
    """
    # 응답에서는 어느 쪽 문제인지 밝히지 않는다. 서버 설정 실수는 로그로 찾는다(키 값은 남기지 않음).
    expected = settings.app_api_key
    if expected is None:
        reason = "서버 키 없음(APP_API_KEY가 설정되지 않음)"
    elif app_key is None:
        reason = "요청 키 없음(X-App-Key 헤더가 없음)"
    elif not secrets.compare_digest(app_key.encode(), expected.get_secret_value().encode()):
        reason = "키 불일치"
    else:
        return
    # 경로는 ID 값 대신 이름으로 바꿔 남긴다(/v1/users/{user_id}).
    path = request.url.path
    for name, value in request.path_params.items():
        path = path.replace(str(value), f"{{{name}}}")
    logger.warning("앱 키 확인 실패: %s. %s %s", reason, request.method, path)
    raise ApiError(401, ErrorCode.invalid_app_key, "X-App-Key가 없거나 맞지 않습니다")
