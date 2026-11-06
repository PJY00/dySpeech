from collections.abc import Hashable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, JsonValue
from pydantic_core import PydanticCustomError


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# 시간대가 있는 ISO 8601 시각만 받고 UTC로 바꿔 둔다. 응답에는 "...Z"로 나간다.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

SampleRate = Literal[16000, 44100, 48000]

# 계약을 바꾸지 않고 앱이 부가 정보를 넣는 자유 JSON 객체. 서버는 저장만 한다.
ClientMeta = Annotated[dict[str, JsonValue], Field(default_factory=dict)]


def require_unique(values: Iterable[Hashable], field: str, item: str) -> None:
    """목록 field 안에서 item 값이 겹치면 거부한다. ctx의 field는 422 응답에서 경로를 목록까지 잇는 데 쓴다."""
    seen: set[Hashable] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(str(value))
        seen.add(value)
    if duplicates:
        raise PydanticCustomError(
            "duplicate",
            "{field}의 {item} 값이 겹칩니다: {values}",
            {"field": field, "item": item, "values": ", ".join(sorted(duplicates))},
        )


class ContractModel(BaseModel):
    """요청·응답 모델의 기준. 계약에 없는 필드가 오면 거부한다."""

    model_config = ConfigDict(extra="forbid")


class Purpose(StrEnum):
    training = "training"
    retest = "retest"
    agc_test = "agc_test"


class SessionStatus(StrEnum):
    open = "open"
    processing = "processing"
    done = "done"
    failed = "failed"


class RecordingKind(StrEnum):
    calibration_glide = "calibration_glide"
    noise_floor = "noise_floor"
    mpt_trial = "mpt_trial"
    eval_sentence = "eval_sentence"
    game = "game"
    agc_tone = "agc_tone"


class Metric(StrEnum):
    max_phonation_time = "max_phonation_time"
    loudness_decay_slope = "loudness_decay_slope"
    mean_level_change = "mean_level_change"
    articulation_rate = "articulation_rate"
    speech_rate = "speech_rate"
    pause_ratio = "pause_ratio"
    f0_sd_semitones = "f0_sd_semitones"
    pitch_range_semitones = "pitch_range_semitones"
    sustained_level = "sustained_level"
    f0_median = "f0_median"
    noise_floor = "noise_floor"


class Unit(StrEnum):
    seconds = "s"
    db_per_second = "dB/s"
    db = "dB"
    dbfs = "dBFS"
    hertz = "Hz"
    semitones = "semitones"
    syllables_per_second = "syllables/s"
    percent = "%"


class DesiredDirection(StrEnum):
    up = "up"
    down = "down"
    none = "none"


class Decision(StrEnum):
    up = "up"
    down = "down"
    hold = "hold"
    deferred = "deferred"


class MdcScale(StrEnum):
    raw = "raw"
    log = "log"


class ChangeStatus(StrEnum):
    no_data = "no_data"
    low_reliability = "low_reliability"
    pending_mdc = "pending_mdc"
    no_change = "no_change"
    increase = "increase"
    decrease = "decrease"


class Mdc(ContractModel):
    value: float
    scale: MdcScale


class ErrorDetail(ContractModel):
    code: str = Field(examples=["SESSION_NOT_FOUND"])
    message: str


class ErrorResponse(ContractModel):
    error: ErrorDetail


class FieldError(ContractModel):
    path: str = Field(examples=["body.recordings[1].trial_no"])
    reason: str = Field(examples=["mpt_trial 녹음에는 trial_no 값이 필요합니다"])


class ValidationErrorDetail(ErrorDetail):
    fields: list[FieldError]


class ValidationErrorResponse(ContractModel):
    error: ValidationErrorDetail
