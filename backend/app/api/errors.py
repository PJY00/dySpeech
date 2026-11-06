from enum import StrEnum
from typing import Any

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


class ErrorCode(StrEnum):
    bad_request = "BAD_REQUEST"
    validation_error = "VALIDATION_ERROR"
    invalid_app_key = "INVALID_APP_KEY"
    user_not_found = "USER_NOT_FOUND"
    session_not_found = "SESSION_NOT_FOUND"
    recording_not_found = "RECORDING_NOT_FOUND"
    id_conflict = "ID_CONFLICT"
    missing_recordings = "MISSING_RECORDINGS"
    not_found = "NOT_FOUND"
    method_not_allowed = "METHOD_NOT_ALLOWED"
    internal_error = "INTERNAL_ERROR"


# 처리기 밖에서 난 HTTP 오류(없는 경로, 허용하지 않는 메서드)의 코드
CODE_BY_STATUS = {404: ErrorCode.not_found, 405: ErrorCode.method_not_allowed}


class ApiError(Exception):
    """계약의 오류 형식으로 응답할 예외."""

    def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_doc(description: str, model: type[ContractModel] = ErrorResponse) -> dict[str, Any]:
    """OpenAPI 문서에 오류 응답을 적는 형식."""
    return {"model": model, "description": description}


INVALID_REQUEST = {422: error_doc("요청이 계약과 다름. fields에 문제 필드의 경로와 이유", ValidationErrorResponse)}


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
    return error_response(exc.status_code, exc.code, exc.message)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [FieldError(path=field_path(error), reason=error["msg"]) for error in exc.errors()]
    detail = ValidationErrorDetail(code=ErrorCode.validation_error, message="요청이 계약과 다릅니다", fields=fields)
    return JSONResponse(status_code=422, content=ValidationErrorResponse(error=detail).model_dump(mode="json"))


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
