from fastapi import APIRouter

from app.api.deps import Db
from app.api.progress import active_mdc
from app.config.metrics import METRICS
from app.schemas.metrics import MetricDefinition, MetricDefinitions

router = APIRouter(tags=["metrics"])


@router.get("/metrics", summary="지표 정의")
def get_metrics(db: Db) -> MetricDefinitions:
    """\f
    GET /v1/metrics: 지표 11개의 단위, 바람직한 방향, 최소 측정 가능 변화량을 돌려줌
    입력: db (요청마다 여는 DB 세션)
    출력: 지표 정의 목록 (METRICS 순서. 지표마다 metric, unit, desired_direction, `mdc`. 활성 값이 없으면 `mdc`는 null)
    알고리즘
    1. 지금 알고리즘 버전의 활성 최소 측정 가능 변화량을 active_mdc로 읽음 (반복 측정 분석이 mdc_values 표에 넣은 값)
    2. METRICS의 지표마다 단위·바람직한 방향과 그 지표의 최소 측정 가능 변화량을 묶어 돌려줌
    """
    # 최소 측정 가능 변화량은 반복 측정 분석이 mdc_values에 넣은 활성 값(지금 알고리즘 버전)이다. 없으면 null이다.
    mdc = active_mdc(db)
    return MetricDefinitions(
        metrics=[
            MetricDefinition(metric=metric, unit=spec.unit, desired_direction=spec.desired_direction, mdc=mdc.get(metric))
            for metric, spec in METRICS.items()
        ]
    )
