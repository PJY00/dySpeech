from fastapi import APIRouter
from pydantic import UUID4

from app.api import examples
from app.api.errors import INVALID_REQUEST, error_doc
from app.schemas.history import History
from app.schemas.users import User, UserIn

router = APIRouter(tags=["users"])


@router.put(
    "/users/{user_id}",
    summary="사용자 등록",
    responses={409: error_doc("같은 user_id에 다른 내용이 이미 있음"), **INVALID_REQUEST},
)
def put_user(user_id: UUID4, body: UserIn) -> User:
    return examples.user(user_id, body)


@router.get(
    "/users/{user_id}/history",
    summary="진전도와 변화 판정",
    responses={404: error_doc("사용자 없음"), **INVALID_REQUEST},
)
def get_history(user_id: UUID4) -> History:
    return examples.history(user_id)
