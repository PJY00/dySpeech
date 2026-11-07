from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import UUID4

from app.api.deps import Db, Storage
from app.api.errors import INVALID_REQUEST, ApiError, ErrorCode, error_doc
from app.db.models import RecordingRow
from app.schemas.recordings import RecordingUrl

router = APIRouter(tags=["recordings"])


@router.get(
    "/recordings/{recording_id}/url",
    summary="녹음 열람 URL 발급",
    responses={404: error_doc("녹음 없음, 또는 보관 기한이 지남"), **INVALID_REQUEST},
)
def get_recording_url(recording_id: UUID4, db: Db, storage: Storage) -> RecordingUrl:
    recording = db.get(RecordingRow, recording_id)
    now = datetime.now(UTC)
    # complete에서 S3 파일을 확인한 녹음만 열람할 수 있다.
    if recording is None or recording.uploaded_at is None or recording.retain_until <= now:
        raise ApiError(404, ErrorCode.recording_not_found, "녹음이 없거나 보관 기한이 지났습니다")
    url = storage.download_url(recording.s3_key, now)
    return RecordingUrl(recording_id=recording_id, url=url.url, expires_at=url.expires_at)
