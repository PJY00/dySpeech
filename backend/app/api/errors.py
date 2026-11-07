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
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def content(self) -> ContractModel:
        return ErrorResponse(error=ErrorDetail(code=self.code, message=self.message))


class RequestRuleError(ApiError):
    """형식은 맞지만 다른 값과 함께 봐야 알 수 있는 명세 위반. 응답은 형식 오류와 같은 422다."""

    def __init__(self, fields: list[FieldError]) -> None:
        super().__init__(422, ErrorCode.validation_error, VALIDATION_MESSAGE)
        self.fields = fields

    def content(self) -> ContractModel:
        return ValidationErrorResponse(
            error=ValidationErrorDetail(code=self.code, message=self.message, fields=self.fields)
        )


class MissingRecordingsError(ApiError):
    def __init__(self, recording_ids: list[UUID]) -> None:
        super().__init__(409, ErrorCode.missing_recordings, "올라오지 않은 녹음이 있습니다")
        self.recording_ids = recording_ids

    def content(self) -> ContractModel:
        return MissingRecordingsResponse(
            error=MissingRecordingsDetail(code=self.code, message=self.message, missing_recording_ids=self.recording_ids)
        )


class InvalidRecordingsError(ApiError):
    def __init__(self, recordings: list[InvalidRecording]) -> None:
        super().__init__(422, ErrorCode.invalid_recording_format, "녹음 파일이 규격과 다릅니다")
        self.recordings = recordings

    def content(self) -> ContractModel:
        return InvalidRecordingsResponse(
            error=InvalidRecordingsDetail(code=self.code, message=self.message, invalid_recordings=self.recordings)
        )


def error_doc(description: str, model: Any = ErrorResponse) -> dict[str, Any]:
    """OpenAPI 문서에 오류 응답을 적는 형식. model은 모델 하나이거나 모델들의 합(A | B)이다."""
    return {"model": model, "description": description}


INVALID_REQUEST = {422: error_doc("요청이 API 명세와 다름. fields에 문제 필드의 경로와 이유", ValidationErrorResponse)}


def field_path(error: dict[str, Any]) -> str:
    """("body", "recordings", 1) → "body.recordings[1]". 모델 검사가 ctx에 남긴 필드가 있으면 뒤에 잇는다."""
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
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"), headers=headers)


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.content().model_dump(mode="json"))


def field_error(error: dict[str, Any]) -> FieldError:
    if error["type"] == "json_invalid":
        # loc의 마지막 값은 필드가 아니라 본문에서 문제가 난 글자 위치다.
        position = error["loc"][-1]
        detail = (error.get("ctx") or {}).get("error", "")
        return FieldError(path="body", reason=f"JSON 형식이 아닙니다(위치 {position}): {detail}")
    return FieldError(path=field_path(error), reason=error["msg"])


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return await handle_api_error(request, RequestRuleError([field_error(error) for error in exc.errors()]))


async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    fallback = ErrorCode.bad_request if exc.status_code < 500 else ErrorCode.internal_error
    code = CODE_BY_STATUS.get(exc.status_code, fallback)
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(500, ErrorCode.internal_error, "서버에서 처리하지 못했습니다")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
