from app.schemas.common import ContractModel, DesiredDirection, Mdc, Metric, Unit


class MetricDefinition(ContractModel):
    metric: Metric
    unit: Unit
    desired_direction: DesiredDirection
    # 아직 정해지지 않았으면 null
    mdc: Mdc | None


class MetricDefinitions(ContractModel):
    metrics: list[MetricDefinition]
