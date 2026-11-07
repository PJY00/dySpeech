from enum import StrEnum
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import (
    ContractModel,
    ErrorDetail,
    ErrorResponse,
    FieldError,
    ValidationErrorDetail,
    ValidationErrorResponse,
)
from app.schemas.sessions import (
    InvalidRecording,
    InvalidRecordingsDetail,
    InvalidRecordingsResponse,
    MissingRecordingsDetail,
    MissingRecordingsResponse,
)


class ErrorCode(StrEnum):
    bad_request = "BAD_REQUEST"
    validation_error = "VALIDATION_ERROR"
    invalid_app_key = "INVALID_APP_KEY"
    user_not_found = "USER_NOT_FOUND"
    session_not_found = "SESSION_NOT_FOUND"
    recording_not_found = "RECORDING_NOT_FOUND"
    id_conflict = "ID_CONFLICT"
    missing_recordings = "MISSING_RECORDINGS"
    payload_too_large = "PAYLOAD_TOO_LARGE"
    invalid_recording_format = "INVALID_RECORDING_FORMAT"
    not_found = "NOT_FOUND"
    method_not_allowed = "METHOD_NOT_ALLOWED"
    internal_error = "INTERNAL_ERROR"


# 처리기 밖에서 난 HTTP 오류(없는 경로, 허용하지 않는 메서드)의 코드
CODE_BY_STATUS = {404: ErrorCode.not_found, 405: ErrorCode.method_not_allowed}

VALIDATION_MESSAGE = "요청이 API 명세와 다릅니다"


class ApiError(Exception):
    """API 명세의 오류 형식으로 응답할 예외."""

    def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
        """
        API 명세의 오류 형식으로 응답할 예외를 초기화함
        입력: status_code (HTTP 상태 코드), code (오류 코드 ErrorCode, 응답의 error.code), message (사람이 읽는 설명, 응답의 error.message)
        출력: 없음 (예외 객체에 status_code, code, message를 둠)
        알고리즘
        1. 예외 메시지를 message로 두고 세 값을 속성으로 저장함
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def content(self) -> ContractModel:
        """
        오류 응답 본문 모델을 만들어 돌려줌
        입력: 없음
        출력: 오류 응답 ErrorResponse ({"error": {"code", "message"}})
        알고리즘
        1. code와 message로 ErrorResponse를 만들어 돌려줌
        """
        return ErrorResponse(error=ErrorDetail(code=self.code, message=self.message))


class RequestRuleError(ApiError):
    """형식은 맞지만 다른 값과 함께 봐야 알 수 있는 명세 위반. 응답은 형식 오류와 같은 422다."""

    def __init__(self, fields: list[FieldError]) -> None:
        """
        다른 값과 함께 봐야 알 수 있는 명세 위반을 422 VALIDATION_ERROR 예외로 초기화함
        입력: fields (문제 필드 목록: 경로 path와 이유 reason)
        출력: 없음 (상태 422, 코드 VALIDATION_ERROR, 메시지 "요청이 API 명세와 다릅니다"인 예외)
        알고리즘
        1. 422, VALIDATION_ERROR, VALIDATION_MESSAGE로 ApiError를 초기화하고 fields를 저장함
        """
        super().__init__(422, ErrorCode.validation_error, VALIDATION_MESSAGE)
        self.fields = fields

    def content(self) -> ContractModel:
        """
        문제 필드 목록을 담은 422 응답 본문을 만들어 돌려줌
        입력: 없음
        출력: ValidationErrorResponse ({"error": {"code", "message", "fields"}})
        알고리즘
        1. code, message, fields로 ValidationErrorResponse를 만들어 돌려줌
        """
        return ValidationErrorResponse(
            error=ValidationErrorDetail(code=self.code, message=self.message, fields=self.fields)
        )


class MissingRecordingsError(ApiError):
    def __init__(self, recording_ids: list[UUID]) -> None:
        """
        complete 때 S3에 올라오지 않은 녹음이 있음을 알리는 409 예외를 초기화함
        입력: recording_ids (S3에 없는 녹음 ID 목록)
        출력: 없음 (상태 409, 코드 MISSING_RECORDINGS, 메시지 "올라오지 않은 녹음이 있습니다"인 예외)
        알고리즘
        1. 409, MISSING_RECORDINGS와 고정 메시지로 ApiError를 초기화하고 recording_ids를 저장함
        """
        super().__init__(409, ErrorCode.missing_recordings, "올라오지 않은 녹음이 있습니다")
        self.recording_ids = recording_ids

    def content(self) -> ContractModel:
        """
        올라오지 않은 녹음 ID를 담은 409 응답 본문을 만들어 돌려줌
        입력: 없음
        출력: MissingRecordingsResponse ({"error": {"code", "message", "missing_recording_ids"}})
        알고리즘
        1. code, message, recording_ids로 응답 모델을 만들어 돌려줌
        """
        return MissingRecordingsResponse(
            error=MissingRecordingsDetail(code=self.code, message=self.message, missing_recording_ids=self.recording_ids)
        )


class InvalidRecordingsError(ApiError):
    def __init__(self, recordings: list[InvalidRecording]) -> None:
        """
        올라온 WAV가 녹음 규격과 다름을 알리는 422 예외를 초기화함
        입력: recordings (규격과 다른 녹음 목록: 녹음 ID와 이유)
        출력: 없음 (상태 422, 코드 INVALID_RECORDING_FORMAT, 메시지 "녹음 파일이 규격과 다릅니다"인 예외)
        알고리즘
        1. 422, INVALID_RECORDING_FORMAT과 고정 메시지로 ApiError를 초기화하고 recordings를 저장함
        """
        super().__init__(422, ErrorCode.invalid_recording_format, "녹음 파일이 규격과 다릅니다")
        self.recordings = recordings

    def content(self) -> ContractModel:
        """
        규격과 다른 녹음과 그 이유를 담은 422 응답 본문을 만들어 돌려줌
        입력: 없음
        출력: InvalidRecordingsResponse ({"error": {"code", "message", "invalid_recordings"}})
        알고리즘
        1. code, message, recordings로 응답 모델을 만들어 돌려줌
        """
        return InvalidRecordingsResponse(
            error=InvalidRecordingsDetail(code=self.code, message=self.message, invalid_recordings=self.recordings)
        )


def error_doc(description: str, model: Any = ErrorResponse) -> dict[str, Any]:
    """
    OpenAPI 문서의 엔드포인트 responses에 적을 오류 응답 항목을 만들어 돌려줌
    입력: description (문서에 적을 오류 설명), model (응답 모델. 모델 하나이거나 모델들의 합(A | B), 기본 ErrorResponse)
    출력: 엔드포인트 responses에 넣는 사전 {"model": model, "description": description}
    알고리즘
    1. 두 값을 사전으로 묶어 돌려줌
    """
    return {"model": model, "description": description}


INVALID_REQUEST = {422: error_doc("요청이 API 명세와 다름. fields에 문제 필드의 경로와 이유", ValidationErrorResponse)}


def field_path(error: dict[str, Any]) -> str:
    """
    검사 오류의 위치(loc)를 422 응답의 필드 경로 문자열로 바꿈
    입력: error (pydantic 검사 오류 하나: loc, 선택 ctx)
    출력: 필드 경로 문자열. 예: ("body", "recordings", 1) → "body.recordings[1]"
    알고리즘
    1. loc을 차례로 이음: 정수는 "[번호]", 문자열은 앞에 "."를 붙임(맨 앞 부분은 그대로)
    2. 모델 검사가 ctx에 남긴 field가 있으면 "."로 뒤에 이음 (예: body.recordings[1].trial_no)
    """
    path = ""
    for part in error["loc"]:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    field = (error.get("ctx") or {}).get("field")
    if field:
        path += f".{field}" if path else field
    return path


def error_response(status_code: int, code: ErrorCode, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """
    API 명세 오류 형식의 JSON 응답을 만들어 돌려줌
    입력: status_code (HTTP 상태 코드), code (오류 코드), message (사람이 읽는 설명), headers (응답 헤더, 선택)
    출력: JSONResponse (본문 {"error": {"code", "message"}})
    알고리즘
    1. ErrorResponse를 JSON으로 바꿔 상태 코드·헤더와 함께 응답으로 돌려줌
    """
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"), headers=headers)


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    """
    ApiError를 그 예외가 정한 상태 코드와 본문의 JSON 응답으로 바꿈
    입력: request (HTTP 요청, 쓰지 않음), exc (처리기에서 난 ApiError 또는 그 하위 예외)
    출력: JSONResponse (상태 exc.status_code, 본문 exc.content())
    알고리즘
    1. 예외의 content()를 JSON으로 바꿔 예외의 상태 코드로 돌려줌
    """
    return JSONResponse(status_code=exc.status_code, content=exc.content().model_dump(mode="json"))


def field_error(error: dict[str, Any]) -> FieldError:
    """
    검사 오류 하나를 422 응답의 필드 오류(경로, 이유)로 바꿈
    입력: error (pydantic 검사 오류 하나: type, loc, msg, 선택 ctx)
    출력: FieldError (path, reason)
    알고리즘
    1. 본문이 JSON이 아니면(type json_invalid) 경로는 "body", 이유는 "JSON 형식이 아닙니다(위치 …): …"로 함. 이때 loc의 마지막 값은 필드가 아니라 본문에서 문제가 난 글자 위치임
    2. 그 밖에는 경로를 field_path로 만들고 이유는 오류의 msg로 함
    """
    if error["type"] == "json_invalid":
        # loc의 마지막 값은 필드가 아니라 본문에서 문제가 난 글자 위치다.
        position = error["loc"][-1]
        detail = (error.get("ctx") or {}).get("error", "")
        return FieldError(path="body", reason=f"JSON 형식이 아닙니다(위치 {position}): {detail}")
    return FieldError(path=field_path(error), reason=error["msg"])


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    FastAPI 요청 검사 오류를 422 VALIDATION_ERROR 응답으로 바꿈
    입력: request (HTTP 요청), exc (요청 검사 오류, 문제 필드 여러 개)
    출력: JSONResponse (422, {"error": {"code": "VALIDATION_ERROR", "message", "fields"}})
    알고리즘
    1. 오류마다 field_error로 경로·이유를 만들어 RequestRuleError로 묶음
    2. handle_api_error로 응답을 만듦
    """
    return await handle_api_error(request, RequestRuleError([field_error(error) for error in exc.errors()]))


async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """
    처리기 밖에서 난 HTTP 오류(없는 경로, 허용하지 않는 메서드 등)를 API 명세 오류 형식으로 바꿈
    입력: request (HTTP 요청), exc (Starlette HTTP 예외: status_code, detail, headers)
    출력: JSONResponse (같은 상태 코드, 본문 {"error": {"code", "message": detail}}, 같은 헤더)
    알고리즘
    1. 오류 코드를 정함: 404는 NOT_FOUND, 405는 METHOD_NOT_ALLOWED, 그 밖에는 500 미만이면 BAD_REQUEST, 500 이상이면 INTERNAL_ERROR
    2. detail을 메시지로 error_response를 만듦
    """
    fallback = ErrorCode.bad_request if exc.status_code < 500 else ErrorCode.internal_error
    code = CODE_BY_STATUS.get(exc.status_code, fallback)
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """
    처리하지 못한 예외를 500 INTERNAL_ERROR 응답으로 바꿈
    입력: request (HTTP 요청), exc (처리하지 못한 예외, 내용은 응답에 넣지 않음)
    출력: JSONResponse (500, 메시지 "서버에서 처리하지 못했습니다")
    알고리즘
    1. 고정 메시지로 error_response를 만듦
    """
    return error_response(500, ErrorCode.internal_error, "서버에서 처리하지 못했습니다")


def register_error_handlers(app: FastAPI) -> None:
    """
    앱에 오류 처리기 네 가지를 붙임
    입력: app (FastAPI 앱)
    출력: 없음 (앱의 예외 처리기를 등록함)
    알고리즘
    1. ApiError → handle_api_error, 요청 검사 오류 → handle_validation_error, HTTP 예외 → handle_http_error, 그 밖의 모든 예외 → handle_unexpected_error를 등록함
    """
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
