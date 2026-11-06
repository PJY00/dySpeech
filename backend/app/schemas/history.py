from typing import Self

from pydantic import UUID4, model_validator

from app.config.metrics import PROGRESS_METRICS
from app.schemas.common import ChangeStatus, ContractModel, DesiredDirection, Mdc, Metric, Unit, UtcDatetime
from app.schemas.sessions import DeviceMeasurement


class BaselinePoint(ContractModel):
    session_id: UUID4
    value: float


class SeriesPoint(ContractModel):
    session_id: UUID4
    started_at: UtcDatetime
    value: float
    low_reliability: bool


class LatestPoint(ContractModel):
    value: float
    # 최근 값 − 기준선 값. mdc.scale이 log이면 퍼센트 변화다.
    change: float


class MetricHistory(ContractModel):
    metric: Metric
    unit: Unit
    desired_direction: DesiredDirection
    baseline: BaselinePoint | None
    series: list[SeriesPoint]
    latest: LatestPoint | None
    mdc: Mdc | None
    status: ChangeStatus

    @model_validator(mode="after")
    def check_no_data(self) -> Self:
        # 값이 없으면 baseline·latest가 null, series가 빈 목록이고 status는 no_data다. 그 반대도 같다.
        present = [self.baseline is not None, self.latest is not None, bool(self.series)]
        if any(present) and not all(present):
            raise ValueError(f"{self.metric}: baseline, latest, series는 모두 비거나 모두 있어야 합니다")
        if (not any(present)) != (self.status is ChangeStatus.no_data):
            raise ValueError(f"{self.metric}: 값이 없을 때만 status가 no_data입니다")
        return self


class DeviceSessionMeasurements(ContractModel):
    session_id: UUID4
    started_at: UtcDatetime
    measurements: list[DeviceMeasurement]


class History(ContractModel):
    user_id: UUID4
    # 진전도 지표를 값이 없어도 빼지 않고, 항상 같은 순서로 모두 넣는다.
    metrics: list[MetricHistory]
    # 최근 3회차의 기기 값. 앱을 다시 설치했을 때 난이도 기준값을 복구하는 용도
    recent_device_measurements: list[DeviceSessionMeasurements]

    @model_validator(mode="after")
    def check_metric_order(self) -> Self:
        if [m.metric for m in self.metrics] != PROGRESS_METRICS:
            raise ValueError(f"metrics는 {', '.join(PROGRESS_METRICS)} 순서로 모두 있어야 합니다")
        return self
