from fastapi import APIRouter
from pydantic import UUID4

from app.api import examples
from app.api.errors import INVALID_REQUEST, error_doc
from app.schemas.recordings import UploadUrlsIn, UploadUrlsOut
from app.schemas.sessions import (
    CompleteAccepted,
    CompleteIn,
    MissingRecordingsResponse,
    Session,
    SessionDetail,
    SessionIn,
)

router = APIRouter(tags=["sessions"])

SESSION_NOT_FOUND = {404: error_doc("세션 없음")}


@router.put(
    "/sessions/{session_id}",
    summary="세션 등록",
    responses={
        404: error_doc("user_id의 사용자 없음"),
        409: error_doc("같은 session_id에 다른 내용이 이미 있음"),
        **INVALID_REQUEST,
    },
)
def put_session(session_id: UUID4, body: SessionIn) -> Session:
    return examples.session(session_id, body)


@router.post(
    "/sessions/{session_id}/upload-urls",
    summary="녹음 업로드 URL 발급",
    responses={
        **SESSION_NOT_FOUND,
        409: error_doc("같은 recording_id에 다른 내용이 이미 있음"),
        **INVALID_REQUEST,
    },
)
def post_upload_urls(session_id: UUID4, body: UploadUrlsIn) -> UploadUrlsOut:
    return examples.upload_urls(session_id, body)


@router.post(
    "/sessions/{session_id}/complete",
    status_code=202,
    summary="세션 완료",
    responses={
        **SESSION_NOT_FOUND,
        409: error_doc(
            "올라오지 않은 녹음이 있음. missing_recording_ids의 파일만 다시 올리고 재요청",
            MissingRecordingsResponse,
        ),
        **INVALID_REQUEST,
    },
)
def post_complete(session_id: UUID4, body: CompleteIn) -> CompleteAccepted:
    return examples.complete(session_id, body)


@router.get(
    "/sessions/{session_id}",
    summary="처리 상태와 서버 지표",
    responses={**SESSION_NOT_FOUND, **INVALID_REQUEST},
)
def get_session(session_id: UUID4) -> SessionDetail:
    return examples.session_detail(session_id)
