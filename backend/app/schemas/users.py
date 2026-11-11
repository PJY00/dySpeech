from pydantic import UUID4

from app.schemas.common import ContractModel, UtcDatetime


class UserIn(ContractModel):
    created_at: UtcDatetime


class User(ContractModel):
    id: UUID4
    created_at: UtcDatetime
