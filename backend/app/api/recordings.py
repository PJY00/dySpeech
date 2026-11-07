from fastapi import APIRouter
from pydantic import UUID4

from app.api import examples
from app.api.errors import INVALID_REQUEST, error_doc
from app.schemas.recordings import RecordingUrl

router = APIRouter(tags=["recordings"])


@router.get(
    "/recordings/{recording_id}/url",
    summary="녹음 열람 URL 발급",
    responses={404: error_doc("녹음 없음, 또는 보관 기한이 지남"), **INVALID_REQUEST},
)
def get_recording_url(recording_id: UUID4) -> RecordingUrl:
    return examples.recording_url(recording_id)
