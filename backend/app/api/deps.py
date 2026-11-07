"""엔드포인트가 쓰는 DB 세션과 녹음 저장소. 테스트는 FastAPI 의존성 덮어쓰기로 바꾼다."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.engine import get_engine
from app.storage.s3 import RecordingStorage, get_storage


def get_db() -> Iterator[Session]:
    """요청마다 새 DB 세션. 처리기가 직접 commit한다. 오류로 끝나면 닫히면서 되돌린다."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def get_recording_storage() -> RecordingStorage:
    return get_storage()


Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[RecordingStorage, Depends(get_recording_storage)]
