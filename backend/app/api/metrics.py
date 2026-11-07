from fastapi import APIRouter

from app.api.deps import Db
from app.api.progress import active_mdc
from app.config.metrics import METRICS
from app.schemas.metrics import MetricDefinition, MetricDefinitions

router = APIRouter(tags=["metrics"])


@router.get("/metrics", summary="지표 정의")
def get_metrics(db: Db) -> MetricDefinitions:
    # 최소 측정 가능 변화량은 반복 측정 분석이 mdc_values에 넣은 활성 값(지금 알고리즘 버전)이다. 없으면 null이다.
    mdc = active_mdc(db)
    return MetricDefinitions(
        metrics=[
            MetricDefinition(metric=metric, unit=spec.unit, desired_direction=spec.desired_direction, mdc=mdc.get(metric))
            for metric, spec in METRICS.items()
        ]
    )
