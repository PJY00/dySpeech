from collections.abc import Hashable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, JsonValue
from pydantic_core import PydanticCustomError


def _to_utc(value: datetime) -> datetime:
    """
    시간대가 있는 시각을 UTC로 바꿈
    입력: value (시간대가 있는 시각)
    출력: 같은 순간의 UTC 시각. 응답에는 "...Z"로 나감
    알고리즘
    1. astimezone(UTC)로 바꿈. UtcDatetime 형식이 시간대 검사 뒤에 이 함수를 부름
    """
    return value.astimezone(UTC)


# 시간대가 있는 ISO 8601 시각만 받고 UTC로 바꿔 둔다. 응답에는 "...Z"로 나간다.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

SampleRate = Literal[16000, 44100, 48000]

# API 명세를 바꾸지 않고 앱이 부가 정보를 넣는 자유 JSON 객체. 서버는 저장만 한다.
ClientMeta = Annotated[dict[str, JsonValue], Field(default_factory=dict)]


def require_unique(values: Iterable[Hashable], field: str, item: str) -> None:
    """
    목록 안에서 같은 값이 겹치면 검사 오류를 냄
    입력: values (검사할 값들), field (목록 필드 이름, 예: recordings), item (항목 필드 이름, 예: recording_id)
    출력: 없음. 겹치면 PydanticCustomError("duplicate", "{field}의 {item} 값이 겹칩니다: …")를 일으킴. ctx의 field는 422 응답에서 경로를 목록까지 잇는 데 쓰임
    알고리즘
    1. 값을 차례로 보며 앞에서 이미 나온 값이면 겹친 값으로 모음
    2. 겹친 값이 있으면 정렬해 쉼표로 이어 오류를 일으킴
    """
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
    """요청·응답 모델의 기준. API 명세에 없는 필드가 오면 거부한다."""

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


class AgcSignal(StrEnum):
    """자동 게인 조절 시험 소리. 시험 소리 정답값 파일의 이름과 같다."""

    agc_steps = "agc_steps"
    agc_hold = "agc_hold"
    agc_toggle = "agc_toggle"


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
