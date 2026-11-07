"""엔드포인트가 쓰는 DB 세션과 녹음 저장소. 테스트는 FastAPI 의존성 덮어쓰기로 바꾼다."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.engine import get_engine
from app.storage.s3 import RecordingStorage, get_storage


def get_db() -> Iterator[Session]:
    """
    요청마다 새 DB 세션을 열어 넘기고, 요청이 끝나면 닫음
    입력: 없음 (DB 엔진 get_engine, 환경 변수 DATABASE_URL)
    출력: DB 세션(SQLAlchemy Session, expire_on_commit=False)을 넘김(yield). 처리기가 직접 commit함. 오류로 끝나면 닫히면서 커밋하지 않은 변경을 되돌림
    알고리즘
    1. get_engine()의 엔진으로 세션을 열어 넘기고, 요청이 끝나면 with 블록이 세션을 닫음
    """
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def get_recording_storage() -> RecordingStorage:
    """
    요청에서 쓸 녹음 저장소(S3)를 돌려줌
    입력: 없음 (get_storage, 환경 변수 S3_BUCKET, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    출력: 녹음 저장소(RecordingStorage). get_storage가 한 번 만든 것을 다시 씀
    알고리즘
    1. get_storage()를 그대로 돌려줌. 테스트는 이 의존성을 FastAPI 의존성 덮어쓰기로 바꿈
    """
    return get_storage()


Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[RecordingStorage, Depends(get_recording_storage)]
