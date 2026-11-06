from typing import Literal, Self

from pydantic import UUID4, Field, FiniteFloat, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ContractModel, RecordingKind, SampleRate, UtcDatetime, require_unique

MAX_DURATION_S = 120

# 녹음 종류마다 꼭 있어야 하는 필드. 다른 종류의 필드는 넣지 않는다.
FIELD_BY_KIND = {
    RecordingKind.mpt_trial: "trial_no",
    RecordingKind.eval_sentence: "sentence_id",
    RecordingKind.game: "game_run_id",
}


class RecordingIn(ContractModel):
    recording_id: UUID4
    kind: RecordingKind
    trial_no: int | None = Field(default=None, ge=1)
    sentence_id: str | None = Field(default=None, min_length=1, max_length=64)
    game_run_id: UUID4 | None = None
    duration_s: FiniteFloat = Field(gt=0, le=MAX_DURATION_S)
    sample_rate: SampleRate
    size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def check_kind_fields(self) -> Self:
        # ctx의 field는 422 응답에서 경로를 문제 필드까지 잇는 데 쓴다.
        required = FIELD_BY_KIND.get(self.kind)
        context = {"kind": str(self.kind)}
        for name in FIELD_BY_KIND.values():
            present = getattr(self, name) is not None
            if name == required and not present:
                raise PydanticCustomError(
                    "kind_field_missing", "{kind} 녹음에는 {field} 값이 필요합니다", context | {"field": name}
                )
            if name != required and present:
                raise PydanticCustomError(
                    "kind_field_not_allowed", "{kind} 녹음에는 {field} 값을 넣지 않습니다", context | {"field": name}
                )
        return self


class UploadUrlsIn(ContractModel):
    recordings: list[RecordingIn] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_ids(self) -> Self:
        require_unique((r.recording_id for r in self.recordings), "recordings", "recording_id")
        return self


class Upload(ContractModel):
    recording_id: UUID4
    # presigned URL은 서명이 깨지지 않도록 문자열 그대로 둔다.
    url: str
    method: Literal["PUT"]
    headers: dict[str, str]
    expires_at: UtcDatetime


class UploadUrlsOut(ContractModel):
    uploads: list[Upload]


class RecordingUrl(ContractModel):
    recording_id: UUID4
    url: str
    expires_at: UtcDatetime
