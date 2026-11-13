from typing import Annotated, Self

from pydantic import UUID4, Field, FiniteFloat, JsonValue, model_validator
from pydantic_core import PydanticCustomError

from app.config.metrics import METRICS
from app.schemas.common import (
    ClientMeta,
    ContractModel,
    Decision,
    ErrorDetail,
    Metric,
    Purpose,
    SampleRate,
    SessionStatus,
    Unit,
    UtcDatetime,
    require_unique,
)

DeviceText = Annotated[str, Field(min_length=1, max_length=100)]


class SessionIn(ContractModel):
    user_id: UUID4
    purpose: Purpose
    started_at: UtcDatetime
    device_model: DeviceText
    os_version: DeviceText
    app_version: DeviceText
    input_device: DeviceText
    sample_rate: SampleRate
    client_meta: ClientMeta


class Session(ContractModel):
    id: UUID4
    user_id: UUID4
    purpose: Purpose
    started_at: UtcDatetime
    ended_at: UtcDatetime | None
    device_model: str
    os_version: str
    app_version: str
    input_device: str
    sample_rate: SampleRate
    status: SessionStatus
    is_baseline: bool


class ServerMeasurement(ContractModel):
    metric: Metric
    value: float
    unit: Unit
    low_reliability: bool
    algorithm_version: str


class SessionDetail(Session):
    # 계산하지 못한 지표는 목록에 넣지 않는다.
    server_measurements: list[ServerMeasurement]
    error: ErrorDetail | None


class DeviceMeasurement(ContractModel):
    metric: Metric
    value: FiniteFloat
    unit: Unit
    low_reliability: bool = False

    @model_validator(mode="after")
    def check_unit(self) -> Self:
        """
        기기 지표의 단위가 그 지표의 정해진 단위와 같은지 검사함
        입력: 없음 (모델 자신: metric, unit)
        출력: 모델 자신. 다르면 PydanticCustomError("unit_mismatch", "{metric}의 단위는 {expected}여야 합니다")를 일으킴. ctx의 field "unit"은 422 응답에서 경로를 unit 필드까지 잇는 데 쓰임
        알고리즘
        1. METRICS에서 그 지표의 단위를 찾아 요청 단위와 비교함
        """
        expected = METRICS[self.metric].unit
        if self.unit != expected:
            raise PydanticCustomError(
                "unit_mismatch",
                "{metric}의 단위는 {expected}여야 합니다",
                {"metric": str(self.metric), "expected": str(expected), "field": "unit"},
            )
        return self


class GameRun(ContractModel):
    game_run_id: UUID4
    game: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    level: int = Field(ge=1)
    round_index: int = Field(ge=1)
    started_at: UtcDatetime
    params: dict[str, JsonValue] = Field(default_factory=dict)
    # 0~100(%). 0~1 비율을 보내는 실수를 이름으로 막는다.
    success_rate_pct: FiniteFloat = Field(ge=0, le=100)
    decision: Decision
    decision_reason: str | None = Field(default=None, max_length=500)
    result: dict[str, JsonValue] = Field(default_factory=dict)


class CompleteIn(ContractModel):
    ended_at: UtcDatetime
    device_measurements: list[DeviceMeasurement] = Field(default_factory=list)
    game_runs: list[GameRun] = Field(default_factory=list)
    client_meta: ClientMeta

    @model_validator(mode="after")
    def check_unique_items(self) -> Self:
        """
        complete 요청 안에서 기기 지표 이름과 게임 라운드 ID가 겹치지 않는지 검사함
        입력: 없음 (모델 자신: device_measurements, game_runs)
        출력: 모델 자신. 겹치면 require_unique의 검사 오류(경로 body.device_measurements 또는 body.game_runs)를 일으킴
        알고리즘
        1. device_measurements의 metric을 require_unique로 검사함
        2. game_runs의 game_run_id를 require_unique로 검사함
        """
        require_unique((m.metric for m in self.device_measurements), "device_measurements", "metric")
        require_unique((g.game_run_id for g in self.game_runs), "game_runs", "game_run_id")
        return self


class CompleteAccepted(ContractModel):
    session_id: UUID4
    status: SessionStatus


class MissingRecordingsDetail(ErrorDetail):
    missing_recording_ids: list[UUID4]


class MissingRecordingsResponse(ContractModel):
    error: MissingRecordingsDetail


class InvalidRecording(ContractModel):
    recording_id: UUID4
    reason: str = Field(examples=["채널이 2개입니다. 모노(1개)여야 합니다"])


class InvalidRecordingsDetail(ErrorDetail):
    invalid_recordings: list[InvalidRecording]


class InvalidRecordingsResponse(ContractModel):
    error: InvalidRecordingsDetail
