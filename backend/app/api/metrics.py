from fastapi import APIRouter

from app.config.metrics import METRICS
from app.schemas.metrics import MetricDefinition, MetricDefinitions

router = APIRouter(tags=["metrics"])


@router.get("/metrics", summary="지표 정의")
def get_metrics() -> MetricDefinitions:
    # 최소 측정 가능 변화량은 반복 측정 분석 뒤에 채운다. 그 전에는 null이다.
    return MetricDefinitions(
        metrics=[
            MetricDefinition(metric=metric, unit=spec.unit, desired_direction=spec.desired_direction, mdc=None)
            for metric, spec in METRICS.items()
        ]
    )
