from enum import StrEnum
from typing import Literal, Self

from pydantic import UUID4, Field, FiniteFloat, model_validator
from pydantic_core import PydanticCustomError

from app.config.sentences import EVAL_SENTENCES
from app.schemas.common import AgcSignal, ContractModel, RecordingKind, SampleRate, UtcDatetime, require_unique

MAX_DURATION_S = 120

# 서버가 정답 음절 수를 알아야 말속도 지표를 계산할 수 있으므로, 서버 설정에 있는 문장 ID만 받는다.
SentenceId = StrEnum("SentenceId", {key: key for key in EVAL_SENTENCES})
SentenceId.__doc__ = "평가 전용 문장 ID. 서버 설정에 있는 값만 받는다."

# 녹음 종류마다 꼭 있어야 하는 필드. 다른 종류의 필드는 넣지 않는다.
FIELD_BY_KIND = {
    RecordingKind.mpt_trial: "trial_no",
    RecordingKind.eval_sentence: "sentence_id",
    RecordingKind.game: "game_run_id",
    RecordingKind.agc_tone: "agc_signal",
}


class RecordingIn(ContractModel):
    recording_id: UUID4
    kind: RecordingKind
    trial_no: int | None = Field(default=None, ge=1)
    sentence_id: SentenceId | None = None
    game_run_id: UUID4 | None = None
    agc_signal: AgcSignal | None = None
    duration_s: FiniteFloat = Field(gt=0, le=MAX_DURATION_S)
    sample_rate: SampleRate
    size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def check_kind_fields(self) -> Self:
        """
        녹음 종류마다 꼭 넣는 필드가 있고 다른 종류의 필드는 없는지 검사함
        입력: 없음 (모델 자신: kind, trial_no, sentence_id, game_run_id, agc_signal)
        출력: 모델 자신. 어긋나면 PydanticCustomError(kind_field_missing 또는 kind_field_not_allowed)를 일으킴. ctx의 field는 422 응답에서 경로를 문제 필드까지 잇는 데 쓰임
        알고리즘
        1. FIELD_BY_KIND에서 이 종류에 꼭 있어야 하는 필드를 찾음 (mpt_trial → trial_no, eval_sentence → sentence_id, game → game_run_id, agc_tone → agc_signal, 나머지 종류는 없음)
        2. 종류별 필드 네 개를 차례로 봄. 꼭 있어야 하는 필드가 없으면 "{kind} 녹음에는 {field} 값이 필요합니다", 다른 종류의 필드가 있으면 "{kind} 녹음에는 {field} 값을 넣지 않습니다" 오류를 일으킴(처음 걸린 하나만)
        """
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
        """
        업로드 URL 요청 안에서 녹음 ID가 겹치지 않는지 검사함
        입력: 없음 (모델 자신: recordings)
        출력: 모델 자신. 겹치면 require_unique의 검사 오류(경로 body.recordings)를 일으킴
        알고리즘
        1. 녹음 ID들을 require_unique로 검사함
        """
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
