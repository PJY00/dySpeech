"""진전도: 훈련 세션의 서버 값(같은 알고리즘 버전)으로 기준선, 회차별 값, 최근 값의 변화와 판정을 만든다.

- 포함 대상: purpose=training이고 서버 처리가 끝난(done) 세션
- 기준선: 그 지표 값이 있는 첫 완료 회차. 보통 is_baseline 회차와 같다.
- 판정 순서: 값 없음 → 최근 값 신뢰 낮음 → 최소 측정 가능 변화량 없음 → |변화량| < 최소 측정 가능 변화량 → 늘어남·줄어듦
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.metrics import METRICS, PROGRESS_METRICS
from app.db.models import MdcValueRow, MeasurementRow, MeasurementSource, SessionRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import ChangeStatus, Mdc, MdcScale, Metric, Purpose, SessionStatus
from app.schemas.history import BaselinePoint, LatestPoint, MetricHistory, SeriesPoint


def active_mdc(db: Session) -> dict[Metric, Mdc]:
    """지금 알고리즘 버전의 활성 최소 측정 가능 변화량. 같은 지표에 여럿이면 가장 최근 것"""
    rows = db.scalars(
        select(MdcValueRow)
        .where(MdcValueRow.active, MdcValueRow.algorithm_version == ALGORITHM_VERSION)
        .order_by(MdcValueRow.computed_at, MdcValueRow.id)
    ).all()
    return {Metric(row.metric): Mdc(value=row.value, scale=row.scale) for row in rows if row.metric in METRICS}


def change_of(baseline: float, latest: float, mdc: Mdc | None) -> float | None:
    """변화량. 로그 척도면 퍼센트 변화. 로그 척도인데 값이 0 이하면 계산할 수 없어 None"""
    if mdc is not None and mdc.scale is MdcScale.log:
        if baseline <= 0 or latest <= 0:
            return None
        return round((latest / baseline - 1) * 100, 4)
    return round(latest - baseline, 4)


def judge(change: float | None, latest_low_reliability: bool, mdc: Mdc | None) -> ChangeStatus:
    if latest_low_reliability:
        return ChangeStatus.low_reliability
    if mdc is None or change is None:
        return ChangeStatus.pending_mdc
    if abs(change) < mdc.value or change == 0:
        return ChangeStatus.no_change
    return ChangeStatus.increase if change > 0 else ChangeStatus.decrease


def metric_histories(db: Session, user_id: UUID) -> list[MetricHistory]:
    rows = db.execute(
        select(MeasurementRow, SessionRow.started_at)
        .join(SessionRow, MeasurementRow.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.purpose == Purpose.training,
            SessionRow.status == SessionStatus.done,
            MeasurementRow.source == MeasurementSource.server,
            MeasurementRow.algorithm_version == ALGORITHM_VERSION,
            MeasurementRow.metric.in_([m.value for m in PROGRESS_METRICS]),
        )
        .order_by(SessionRow.started_at, SessionRow.id)
    ).all()
    series: dict[Metric, list[SeriesPoint]] = {metric: [] for metric in PROGRESS_METRICS}
    for row, started_at in rows:
        series[Metric(row.metric)].append(
            SeriesPoint(session_id=row.session_id, started_at=started_at, value=row.value, low_reliability=row.low_reliability)
        )
    mdc = active_mdc(db)
    histories = []
    for metric in PROGRESS_METRICS:
        spec, points, metric_mdc = METRICS[metric], series[metric], mdc.get(metric)
        common = {"metric": metric, "unit": spec.unit, "desired_direction": spec.desired_direction, "mdc": metric_mdc}
        if not points:
            histories.append(MetricHistory(**common, baseline=None, series=[], latest=None, status=ChangeStatus.no_data))
            continue
        first, last = points[0], points[-1]
        change = change_of(first.value, last.value, metric_mdc)
        histories.append(
            MetricHistory(
                **common,
                baseline=BaselinePoint(session_id=first.session_id, value=first.value),
                series=points,
                # 로그 척도인데 변화량을 구할 수 없으면 원 척도 차이를 보여준다(판정은 pending_mdc).
                latest=LatestPoint(value=last.value, change=change if change is not None else round(last.value - first.value, 4)),
                status=judge(change, last.low_reliability, metric_mdc),
            )
        )
    return histories
