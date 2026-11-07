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
    """
    지금 알고리즘 버전의 활성 최소 측정 가능 변화량을 지표별로 모음
    입력: db (DB 세션)
    출력: 지표 → 최소 측정 가능 변화량(value, scale: raw 또는 log) 사전. 값이 없는 지표는 빠짐
    알고리즘
    1. mdc_values 표에서 active이고 algorithm_version이 지금 버전(ALGORITHM_VERSION)인 행을 계산 시각·ID 순으로 읽음
    2. METRICS에 있는 지표만 사전에 넣음. 같은 지표에 여럿이면 뒤에 나온 가장 최근 것이 남음
    """
    rows = db.scalars(
        select(MdcValueRow)
        .where(MdcValueRow.active, MdcValueRow.algorithm_version == ALGORITHM_VERSION)
        .order_by(MdcValueRow.computed_at, MdcValueRow.id)
    ).all()
    return {Metric(row.metric): Mdc(value=row.value, scale=row.scale) for row in rows if row.metric in METRICS}


def change_of(baseline: float, latest: float, mdc: Mdc | None) -> float | None:
    """
    기준선 값에서 최근 값까지의 변화량을 계산함
    입력: baseline (기준선 값, 지표 단위), latest (최근 값, 지표 단위), `mdc` (그 지표의 최소 측정 가능 변화량, 없으면 None)
    출력: 변화량(소수 넷째 자리까지 반올림). 로그 척도면 퍼센트 변화(%), 아니면 지표 단위. 로그 척도인데 값이 0 이하면 계산할 수 없어 None
    알고리즘
    1. `mdc.scale`이 log이면 Δ%(퍼센트 변화) = (x_latest(최근 값) ÷ x_baseline(기준선 값) − 1) × 100. 두 값 중 하나라도 0 이하면 None
    2. 그 밖(raw이거나 `mdc` 없음)에는 Δ(변화량) = x_latest(최근 값) − x_baseline(기준선 값)
    """
    if mdc is not None and mdc.scale is MdcScale.log:
        if baseline <= 0 or latest <= 0:
            return None
        return round((latest / baseline - 1) * 100, 4)
    return round(latest - baseline, 4)


def judge(change: float | None, latest_low_reliability: bool, mdc: Mdc | None) -> ChangeStatus:
    """
    최근 값의 변화 판정을 정함
    입력: change (변화량, change_of 결과. 계산할 수 없으면 None), latest_low_reliability (최근 값이 신뢰 낮음인지), `mdc` (최소 측정 가능 변화량, 없으면 None)
    출력: 판정 ChangeStatus (low_reliability, pending_mdc, no_change, increase, decrease 중 하나. no_data는 부르는 쪽이 정함)
    알고리즘
    1. 최근 값이 신뢰 낮음이면 low_reliability
    2. 최소 측정 가능 변화량이 없거나 변화량을 계산하지 못했으면 pending_mdc
    3. |변화량| < 최소 측정 가능 변화량이거나 변화량이 0이면 no_change
    4. 그 밖에는 변화량이 양수면 increase, 음수면 decrease
    """
    if latest_low_reliability:
        return ChangeStatus.low_reliability
    if mdc is None or change is None:
        return ChangeStatus.pending_mdc
    if abs(change) < mdc.value or change == 0:
        return ChangeStatus.no_change
    return ChangeStatus.increase if change > 0 else ChangeStatus.decrease


def metric_histories(db: Session, user_id: UUID) -> list[MetricHistory]:
    """
    사용자의 진전도 지표마다 기준선, 회차별 값, 최근 값의 변화와 판정을 모음
    입력: db (DB 세션), user_id (사용자 ID)
    출력: 진전도 지표 8개의 진전도 목록 (PROGRESS_METRICS 순서. 값이 없는 지표는 baseline·latest null, series 빈 목록, status no_data)
    알고리즘
    1. 이 사용자의 훈련(training) 세션 중 done인 세션의 서버 값을, 지금 알고리즘 버전과 진전도 지표로 한정해 세션 시작 시각·세션 ID 순으로 읽음
    2. 지표별로 회차 값(세션 ID, 시작 시각, 값, 신뢰 낮음)을 모음
    3. 활성 최소 측정 가능 변화량을 active_mdc로 읽음
    4. 지표마다 값이 없으면 no_data로 둠. 있으면 첫 회차를 기준선, 마지막 회차를 최근 값으로 두고 change_of와 judge로 변화량·판정을 구함
    5. 로그 척도인데 변화량을 구할 수 없으면 latest.change에는 원 척도 차이(최근 값 − 기준선 값)를 넣음(판정은 pending_mdc)
    """
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
