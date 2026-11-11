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
    """\f
    GET /v1/recordings/{recording_id}/url: 녹음을 들어 볼 수 있는 10분짜리 presigned GET URL을 돌려줌
    입력: recording_id (녹음 ID, UUID v4), db (요청마다 여는 DB 세션), storage (녹음 저장소, S3)
    출력: 녹음 열람 URL(recording_id, url, expires_at = 지금 + 10분). 녹음이 없거나 complete 전이거나 보관 기한이 지났으면 404 RECORDING_NOT_FOUND
    알고리즘
    1. 녹음 행을 읽음
    2. 행이 없거나, complete에서 S3 파일을 확인하지 않았거나(uploaded_at 없음), 보관 기한(retain_until)이 지났으면 404 RECORDING_NOT_FOUND 오류를 일으킴
    3. 저장 위치(s3_key)로 내려받기 URL을 만들어 돌려줌(유효 시간 10분)
    """
    recording = db.get(RecordingRow, recording_id)
    now = datetime.now(UTC)
    # complete에서 S3 파일을 확인한 녹음만 열람할 수 있다.
    if recording is None or recording.uploaded_at is None or recording.retain_until <= now:
        raise ApiError(404, ErrorCode.recording_not_found, "녹음이 없거나 보관 기한이 지났습니다")
    url = storage.download_url(recording.s3_key, now)
    return RecordingUrl(recording_id=recording_id, url=url.url, expires_at=url.expires_at)
