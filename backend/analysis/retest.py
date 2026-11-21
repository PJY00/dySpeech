"""반복 측정으로 지표별 최소 측정 가능 변화량을 계산한다.

같은 사람이 서로 다른 날 같은 측정을 되풀이하면 값이 조금씩 달라진다. 이 흔들림보다 큰 변화만 "변했다"고 볼 수 있다.
이 도구는 훈련 회차(측정 블록 → 게임)의 측정 블록 서버 값을 읽어 그 기준(최소 측정 가능 변화량)을 지표별로 계산한다.
backend 폴더에서 실행한다. DB는 .env(또는 환경 변수)의 운영 접속 정보를 쓴다. S3는 쓰지 않는다.
    python -m analysis.retest                                  계산 결과만 보여 준다(쓰지 않음)
    --same-speaker 사용자ID=사용자ID   두 사용자 ID를 한 화자로 묶는다(앱을 다시 설치해 ID가 바뀐 경우). 여러 번 쓸 수 있다
    --exclude-speaker 사용자ID         이 화자의 회차를 모두 뺀다(측정 조건이 달랐던 화자). 여러 번 쓸 수 있다
    --log 지표,지표                    이 지표는 로그 척도 값으로 반영한다. 나머지는 원 척도
    --detail                           회차별 값도 함께 보여 준다
    --json                             JSON으로 출력한다(기본은 마크다운 표)
    --yes                              진전도 지표의 값을 mdc_values에 활성으로 넣는다. 같은 지표의 이전 활성 값은 끈다
    --force                            화자·날 수 조건(4명 × 서로 다른 날 3회)을 채우지 못해도 반영한다

- 대상: done 훈련 회차 중 개발용(client_meta의 dev가 true)이 아닌 것. 한 화자가 하루(한국 시간)에 여러 회차를 했으면 그날 첫 회차만 쓴다.
- 값: 지금 알고리즘 버전의 서버 값. 신뢰 낮음 값은 뺀다. 날이 2일보다 적은 화자는 그 지표 계산에서 빠진다.
- MS_W(개인 내 평균제곱) = Σᵢ Σⱼ (x_ij(화자 i의 j번째 날 값) − x̄ᵢ(화자 i의 평균))² ÷ Σᵢ (kᵢ(화자 i의 날 수) − 1)
  화자마다 날 수가 같으면 분모는 n(화자 수) × (k(날 수) − 1)이다.
- SEM(측정 표준오차) = √MS_W, MDC95(95% 수준 최소 측정 가능 변화량) = 1.96 × √2 × SEM
- 로그 척도: 값의 자연로그로 같은 계산을 한 뒤 퍼센트로 바꾼다. MDC95%(퍼센트) = (exp(1.96 × √2 × SEM_ln(로그 값의 측정 표준오차)) − 1) × 100.
  값이 모두 0보다 클 때만 계산한다.
- 평균 음량 변화는 기준 회차와의 차이라서, 그 바탕인 평가 문장 발화 음량(세부 정보의 sentence_level_dbfs, dBFS)으로 계산한다.
  한 화자 안에서는 기준 회차 음량이 상수라 흔들림이 같고, 사용자 ID가 바뀌어 기준 회차가 달라진 화자도 바르게 계산된다.
- 모든 화자의 값이 날마다 같아 측정 표준오차가 0인 지표는 반영하지 않는다. 0을 넣으면 아주 작은 차이도 변화로 판정되기 때문이다.
- 표준편차 최대÷최소: 화자별 표준편차 중 가장 큰 값 ÷ 가장 작은 값. 1에 가까울수록 한 가지 기준이 모든 화자에게 고르게 맞는다. 척도를 고를 때 본다.
- 급내상관계수(2원 무작위 효과, 절대 일치, 단일 측정)는 참고값이다. 날 수가 같아야 해서 3일 이상인 화자의 앞 3일로 계산한다.
- 건강한 성인의 값이라 환자보다 작게 나올 수 있다.
"""

import argparse
import json
import math
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config.metrics import METRICS, PROGRESS_METRICS
from app.db.models import MdcValueRow, MeasurementRow, MeasurementSource, SessionRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import MdcScale, Metric, Purpose, SessionStatus

KST = timezone(timedelta(hours=9), "KST")  # 날짜를 나누는 시간대
Z_95 = 1.96  # 95% 수준의 배수
MIN_DAYS = 2  # 화자 한 명이 계산에 들어가려면 필요한 날 수
ICC_DAYS = 3  # 급내상관계수에 쓰는 날 수(화자마다 앞에서부터)
PROTOCOL_SPEAKERS = 4  # 반영에 필요한 화자 수
PROTOCOL_DAYS = 3  # 반영에 필요한 화자별 날 수
METHOD = "within_subject_sem"  # mdc_values.method에 적는 계산 방식 이름
LEVEL_DETAIL = "sentence_level_dbfs"  # 평균 음량 변화 대신 읽는 세부 정보 키
STORED_DIGITS = 4  # mdc_values.value에 남기는 소수 자리


@dataclass(frozen=True)
class Visit:
    speaker: uuid.UUID  # 묶은 뒤의 화자(묶음의 첫 사용자 ID)
    user_id: uuid.UUID
    session_id: uuid.UUID
    started_at: datetime  # 한국 시간
    input_device: str
    values: dict[str, float]  # 지표 이름 → 계산에 쓰는 값


@dataclass(frozen=True)
class Excluded:
    dev_sessions: int  # 개발용이라 뺀 회차 수
    excluded_speaker_sessions: int  # 빼기로 한 화자의 회차라 뺀 회차 수
    later_same_day: int  # 같은 날 첫 회차가 아니라 뺀 회차 수
    low_reliability_values: int  # 신뢰 낮음이라 뺀 값 수


@dataclass(frozen=True)
class MetricResult:
    metric: str
    unit: str
    used_for_progress: bool
    speakers: int  # 날이 2일 이상이라 계산에 들어간 화자 수
    days_per_speaker: list[int]  # 계산에 들어간 화자별 날 수(많은 순)
    values: int
    degrees_of_freedom: int
    mean: float | None
    sem_raw: float | None
    mdc_raw: float | None
    sd_ratio_raw: float | None
    sem_log: float | None  # 자연로그 값의 측정 표준오차
    mdc_log_pct: float | None
    sd_ratio_log: float | None
    icc: float | None
    icc_speakers: int


@dataclass(frozen=True)
class Selection:
    metric: str
    value: float
    scale: MdcScale
    n_speakers: int
    n_repeats: int  # 화자별 날 수 중 가장 적은 값
    note: str


def parse_group(text: str) -> list[uuid.UUID]:
    """
    --same-speaker 값을 사용자 ID 목록으로 바꿈
    입력: text ("사용자ID=사용자ID" 꼴. "="로 셋 이상 이어도 됨)
    출력: 사용자 ID 목록(적힌 순서). 2개보다 적거나 ID 형식이 아니면 argparse.ArgumentTypeError
    알고리즘
    1. "="로 나눠 각각 UUID로 읽음
    2. 2개 이상인지 확인함
    """
    try:
        ids = [uuid.UUID(part.strip()) for part in text.split("=")]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"사용자 ID 형식이 아닙니다: {text}") from error
    if len(ids) < 2:
        raise argparse.ArgumentTypeError("사용자ID=사용자ID 꼴로 두 개 이상 적어야 합니다")
    return ids


def speaker_map(groups: list[list[uuid.UUID]]) -> dict[uuid.UUID, uuid.UUID]:
    """
    한 화자로 묶을 사용자 ID 묶음을 "사용자 ID → 화자" 표로 바꿈
    입력: groups (묶음 목록. 묶음마다 사용자 ID 목록)
    출력: 사전(사용자 ID → 그 묶음의 첫 사용자 ID). 한 사용자 ID가 두 묶음에 있으면 ValueError
    알고리즘
    1. 묶음마다 첫 ID를 화자로 삼아 나머지 ID를 그 화자에 이음
    2. 이미 다른 화자에 이어진 ID가 또 나오면 오류를 냄
    """
    mapping: dict[uuid.UUID, uuid.UUID] = {}
    for group in groups:
        for user_id in group:
            if mapping.get(user_id, group[0]) != group[0]:
                raise ValueError(f"사용자 ID {user_id}가 두 묶음에 들어 있습니다")
            mapping[user_id] = group[0]
    return mapping


def is_dev(meta: dict | None, complete_meta: dict | None) -> bool:
    """
    개발용 회차인지 봄
    입력: meta (세션 등록 때의 client_meta), complete_meta (complete 요청의 client_meta). 둘 다 없을 수 있음
    출력: 둘 중 하나라도 dev가 true면 True
    알고리즘
    1. 두 사전의 dev 값을 확인함
    """
    return (meta or {}).get("dev") is True or (complete_meta or {}).get("dev") is True


def load_visits(
    db: Session, same_speaker: dict[uuid.UUID, uuid.UUID] | None = None, exclude: set[uuid.UUID] | None = None
) -> tuple[list[Visit], Excluded]:
    """
    화자마다 하루에 한 회차씩, 계산에 쓸 서버 값을 읽음. 읽기만 함
    입력: db (DB 세션), same_speaker (speaker_map이 만든 "사용자 ID → 화자" 표. 없으면 사용자 ID가 곧 화자), exclude (뺄 화자의 사용자 ID 집합. 묶인 ID 중 어느 것을 적어도 됨)
    출력: (Visit 목록(시작 시각·세션 ID 순. 시작 시각은 한국 시간), Excluded(뺀 개발용 회차 수, 뺀 화자의 회차 수, 뺀 같은 날 뒤 회차 수, 뺀 신뢰 낮음 값 수))
    알고리즘
    1. done 훈련 회차를 시작 시각·ID 순으로 읽음
    2. 개발용 회차(is_dev)를 빼고 수를 셈
    3. 사용자 ID를 화자로 바꾼 뒤, 빼기로 한 화자(exclude)의 회차를 빼고 수를 셈
    4. 화자와 한국 시간 날짜가 같은 회차 중 첫 회차만 남기고 나머지 수를 셈
    5. 남은 회차의 서버 측정값(source = server, 알고리즘 버전이 지금 버전 ALGORITHM_VERSION) 중 정의된 지표(METRICS)를 읽음
    6. 신뢰 낮음 값은 빼고 수를 셈
    7. 평균 음량 변화는 값 대신 세부 정보의 sentence_level_dbfs(평가 문장 발화 음량, dBFS)를 씀. 없으면 그 값은 뺌
    """
    same_speaker = same_speaker or {}
    excluded_speakers = {same_speaker.get(user_id, user_id) for user_id in exclude or ()}
    sessions = db.execute(
        select(
            SessionRow.id, SessionRow.user_id, SessionRow.started_at, SessionRow.input_device,
            SessionRow.client_meta, SessionRow.complete_client_meta,
        )
        .where(SessionRow.status == SessionStatus.done, SessionRow.purpose == Purpose.training)
        .order_by(SessionRow.started_at, SessionRow.id)
    ).all()

    chosen: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, datetime, str]] = {}
    seen_days: set[tuple[uuid.UUID, date]] = set()
    dev_sessions = speaker_sessions = later_same_day = 0
    for session_id, user_id, started_at, input_device, meta, complete_meta in sessions:
        if is_dev(meta, complete_meta):
            dev_sessions += 1
            continue
        speaker = same_speaker.get(user_id, user_id)
        if speaker in excluded_speakers:
            speaker_sessions += 1
            continue
        started = started_at.astimezone(KST)
        day = (speaker, started.date())
        if day in seen_days:
            later_same_day += 1
            continue
        seen_days.add(day)
        chosen[session_id] = (speaker, user_id, started, input_device)

    values: dict[uuid.UUID, dict[str, float]] = {session_id: {} for session_id in chosen}
    low_values = 0
    known = {metric.value for metric in METRICS}
    if chosen:
        measurements = db.execute(
            select(MeasurementRow.session_id, MeasurementRow.metric, MeasurementRow.value, MeasurementRow.low_reliability, MeasurementRow.details)
            .where(
                MeasurementRow.session_id.in_(list(chosen)),
                MeasurementRow.source == MeasurementSource.server,
                MeasurementRow.algorithm_version == ALGORITHM_VERSION,
            )
        )
        for session_id, metric, value, low, details in measurements:
            if metric not in known:
                continue
            if low:
                low_values += 1
                continue
            if metric == Metric.mean_level_change.value:
                value = (details or {}).get(LEVEL_DETAIL)
            if value is None:
                continue
            values[session_id][metric] = float(value)

    visits = [
        Visit(speaker, user_id, session_id, started, input_device, values[session_id])
        for session_id, (speaker, user_id, started, input_device) in chosen.items()
    ]
    return visits, Excluded(dev_sessions, speaker_sessions, later_same_day, low_values)


def within_sem(groups: list[np.ndarray]) -> tuple[float, int] | None:
    """
    화자별 값 묶음에서 측정 표준오차를 구함
    입력: groups (화자마다 그 화자의 날별 값 배열. 배열마다 값이 2개 이상)
    출력: (SEM(측정 표준오차, 값의 단위), 자유도). 묶음이 없으면 None
    알고리즘
    1. MS_W(개인 내 평균제곱) = Σᵢ Σⱼ (x_ij(화자 i의 j번째 값) − x̄ᵢ(화자 i의 평균))² ÷ Σᵢ (kᵢ(화자 i의 값 수) − 1)
    2. SEM = √MS_W
    """
    freedom = sum(len(group) - 1 for group in groups)
    if freedom <= 0:
        return None
    squares = sum(float(((group - group.mean()) ** 2).sum()) for group in groups)
    return math.sqrt(squares / freedom), freedom


def sd_ratio(groups: list[np.ndarray]) -> float | None:
    """
    화자별 표준편차가 얼마나 고른지 구함
    입력: groups (화자마다 그 화자의 날별 값 배열. 배열마다 값이 2개 이상)
    출력: 화자별 표본 표준편차(n − 1로 나눔) 중 가장 큰 값 ÷ 가장 작은 값. 화자가 2명보다 적거나 가장 작은 값이 0이면 None
    알고리즘
    1. 화자마다 표본 표준편차를 구함
    2. 최댓값을 최솟값으로 나눔
    """
    if len(groups) < 2:
        return None
    deviations = [float(group.std(ddof=1)) for group in groups]
    return max(deviations) / min(deviations) if min(deviations) > 0 else None


def icc_absolute_single(table: np.ndarray) -> float | None:
    """
    급내상관계수(2원 무작위 효과, 절대 일치, 단일 측정)를 구함
    입력: table (행 = 화자, 열 = 날 순서인 값 배열. 빈 칸 없음)
    출력: 급내상관계수(단위 없음. 음수가 나올 수 있음). 화자나 날이 2보다 적거나 분모가 0이면 None
    알고리즘
    1. MS_R(화자 간 평균제곱), MS_C(날 간 평균제곱), MS_E(잔차 평균제곱)를 2원 분산분석으로 구함
    2. ICC = (MS_R − MS_E) ÷ (MS_R + (k(날 수) − 1) × MS_E + k × (MS_C − MS_E) ÷ n(화자 수))
    """
    n, k = table.shape
    if n < 2 or k < 2:
        return None
    grand = table.mean()
    rows = k * float(((table.mean(axis=1) - grand) ** 2).sum())
    columns = n * float(((table.mean(axis=0) - grand) ** 2).sum())
    residual = float(((table - grand) ** 2).sum()) - rows - columns
    ms_rows, ms_columns, ms_error = rows / (n - 1), columns / (k - 1), residual / ((n - 1) * (k - 1))
    denominator = ms_rows + (k - 1) * ms_error + k * (ms_columns - ms_error) / n
    return (ms_rows - ms_error) / denominator if denominator != 0 else None


def analyze(visits: list[Visit]) -> list[MetricResult]:
    """
    지표마다 측정 표준오차와 최소 측정 가능 변화량을 원 척도와 로그 척도로 계산함
    입력: visits (load_visits가 읽은 회차)
    출력: MetricResult 목록(지표 정의 순, 11개 모두). 계산할 수 없는 칸은 None
    알고리즘
    1. 지표마다 화자별로 날짜 순 값을 모으고, 값이 2개(MIN_DAYS)보다 적은 화자는 뺌
    2. 원 척도: SEM(측정 표준오차)을 within_sem으로 구하고 MDC95(최소 측정 가능 변화량) = 1.96 × √2 × SEM
    3. 로그 척도(값이 모두 0보다 클 때만): 자연로그 값으로 SEM_ln을 구하고 MDC95%(퍼센트) = (exp(1.96 × √2 × SEM_ln) − 1) × 100
    4. 두 척도마다 화자별 표준편차의 최대 ÷ 최소(sd_ratio)를 구함
    5. 값이 3개(ICC_DAYS) 이상인 화자의 앞 3개로 급내상관계수(icc_absolute_single)를 구함
    """
    results = []
    for metric, spec in METRICS.items():
        by_speaker: dict[uuid.UUID, list[float]] = {}
        for visit in sorted(visits, key=lambda item: (item.started_at, str(item.session_id))):
            if metric.value in visit.values:
                by_speaker.setdefault(visit.speaker, []).append(visit.values[metric.value])
        groups = [np.array(series) for series in by_speaker.values() if len(series) >= MIN_DAYS]
        raw = within_sem(groups)
        positive = bool(groups) and all((group > 0).all() for group in groups)
        logs = [np.log(group) for group in groups] if positive else []
        log = within_sem(logs)
        full = [group[:ICC_DAYS] for group in groups if len(group) >= ICC_DAYS]
        results.append(
            MetricResult(
                metric=metric.value,
                unit=spec.unit.value,
                used_for_progress=spec.used_for_progress,
                speakers=len(groups),
                days_per_speaker=sorted((len(group) for group in groups), reverse=True),
                values=sum(len(group) for group in groups),
                degrees_of_freedom=raw[1] if raw else 0,
                mean=float(np.concatenate(groups).mean()) if groups else None,
                sem_raw=raw[0] if raw else None,
                mdc_raw=Z_95 * math.sqrt(2) * raw[0] if raw else None,
                sd_ratio_raw=sd_ratio(groups),
                sem_log=log[0] if log else None,
                mdc_log_pct=(math.exp(Z_95 * math.sqrt(2) * log[0]) - 1) * 100 if log else None,
                sd_ratio_log=sd_ratio(logs),
                icc=icc_absolute_single(np.array(full)) if full else None,
                icc_speakers=len(full),
            )
        )
    return results


def select_values(results: list[MetricResult], log_metrics: set[str], excluded_speakers: int = 0) -> list[Selection]:
    """
    mdc_values에 넣을 값을 고름
    입력: results (analyze 결과), log_metrics (로그 척도로 반영할 지표 이름 집합), excluded_speakers (--exclude-speaker로 뺀 화자 수)
    출력: Selection 목록(진전도 지표 중 계산됐고 측정 표준오차가 0보다 큰 것, 지표 정의 순). log_metrics에 진전도 지표가 아니거나 로그 척도로 계산되지 않은 지표가 있으면 ValueError
    알고리즘
    1. log_metrics의 이름이 모두 진전도 지표(PROGRESS_METRICS)인지 확인함
    2. 진전도 지표마다 log_metrics에 있으면 로그 척도 값(%), 없으면 원 척도 값을 고름. 소수 넷째 자리로 반올림함
       계산되지 않았거나 측정 표준오차가 0인(값이 날마다 같은) 지표는 건너뜀
    3. 설명(note)에 자료 종류, 화자별 날 수, 값 수, 자유도를 적음. 뺀 화자가 있으면 그 수를, 평균 음량 변화는 평가 문장 발화 음량으로 계산했다고 덧붙임
    """
    progress = {metric.value for metric in PROGRESS_METRICS}
    unknown = sorted(log_metrics - progress)
    if unknown:
        raise ValueError(f"--log에는 진전도 지표만 쓸 수 있습니다: {', '.join(unknown)}")
    selections = []
    for item in results:
        if item.metric not in progress or not item.mdc_raw:
            continue  # 측정 표준오차가 0이면 흔들림을 재지 못한 것이라 기준으로 쓰지 않는다
        use_log = item.metric in log_metrics
        if use_log and item.mdc_log_pct is None:
            raise ValueError(f"{item.metric}은 0 이하인 값이 있어 로그 척도로 계산되지 않았습니다")
        days = "·".join(str(count) for count in item.days_per_speaker)
        note = f"훈련 회차의 측정 블록(그날 첫 회차), 건강한 성인. 화자별 날 수 {days}, 값 {item.values}개, 자유도 {item.degrees_of_freedom}"
        if excluded_speakers:
            note += f". 화자 {excluded_speakers}명 제외"
        if item.metric == Metric.mean_level_change.value:
            note += ". 평가 문장 발화 음량(dBFS)으로 계산"
        selections.append(
            Selection(
                metric=item.metric,
                value=round(item.mdc_log_pct if use_log else item.mdc_raw, STORED_DIGITS),
                scale=MdcScale.log if use_log else MdcScale.raw,
                n_speakers=item.speakers,
                n_repeats=min(item.days_per_speaker),
                note=note,
            )
        )
    return selections


def days_by_speaker(visits: list[Visit]) -> dict[uuid.UUID, list[Visit]]:
    """
    회차를 화자별로 묶음
    입력: visits (load_visits가 읽은 회차)
    출력: 사전(화자 → 그 화자의 회차 목록, 시작 시각 순). 화자는 첫 회차가 이른 순
    알고리즘
    1. 시작 시각·세션 ID 순으로 정렬해 화자별로 모음
    """
    grouped: dict[uuid.UUID, list[Visit]] = {}
    for visit in sorted(visits, key=lambda item: (item.started_at, str(item.session_id))):
        grouped.setdefault(visit.speaker, []).append(visit)
    return grouped


def protocol_met(visits: list[Visit]) -> bool:
    """
    화자·날 수 조건을 채웠는지 봄
    입력: visits (load_visits가 읽은 회차)
    출력: 날이 3일(PROTOCOL_DAYS) 이상인 화자가 4명(PROTOCOL_SPEAKERS) 이상이면 True
    알고리즘
    1. 화자별 회차 수를 세어 조건과 비교함
    """
    enough = sum(len(days) >= PROTOCOL_DAYS for days in days_by_speaker(visits).values())
    return enough >= PROTOCOL_SPEAKERS


def apply_values(db: Session, selections: list[Selection]) -> int:
    """
    고른 값을 mdc_values에 활성으로 넣음
    입력: db (DB 세션), selections (select_values가 고른 값)
    출력: 넣은 행 수. DB에 씀: 같은 지표·지금 알고리즘 버전의 이전 활성 행을 끄고(active = false) 새 행을 넣은 뒤 커밋함
    알고리즘
    1. 지표마다 지금 알고리즘 버전(ALGORITHM_VERSION)의 활성 행을 비활성으로 바꿈
    2. 새 행(값, 척도, 계산 방식, 화자 수, 날 수, 설명, 활성)을 넣음
    3. 한 번에 커밋함
    """
    for item in selections:
        db.execute(
            update(MdcValueRow)
            .where(MdcValueRow.metric == item.metric, MdcValueRow.algorithm_version == ALGORITHM_VERSION, MdcValueRow.active)
            .values(active=False)
        )
        db.add(
            MdcValueRow(
                metric=item.metric, value=item.value, scale=item.scale, method=METHOD, n_speakers=item.n_speakers,
                n_repeats=item.n_repeats, algorithm_version=ALGORITHM_VERSION, active=True, note=item.note,
            )
        )
    db.commit()
    return len(selections)


def number(value: float | None, digits: int = 3, suffix: str = "") -> str:
    """
    표에 넣을 수를 적음
    입력: value (값 또는 None), digits (소수 자리), suffix (뒤에 붙일 글자, 예: "%")
    출력: 소수 digits자리 문자열에 suffix를 붙인 것. None이면 "-"
    알고리즘
    1. None이면 "-", 아니면 자리 수에 맞춰 적음
    """
    return "-" if value is None else f"{value:.{digits}f}{suffix}"


def render(visits: list[Visit], results: list[MetricResult], selections: list[Selection], excluded: Excluded, detail: bool) -> str:
    """
    계산 결과를 마크다운 표로 만듦
    입력: visits (계산에 쓴 회차), results (지표별 결과), selections (반영할 값), excluded (뺀 회차·값 수), detail (회차별 값 표도 넣을지)
    출력: 여러 줄 문자열. 머리말, 화자 표, 지표별 결과 표, detail이면 회차별 값 표. 회차가 없으면 머리말과 "계산할 회차가 없습니다" 한 줄
    알고리즘
    1. 머리말에 알고리즘 버전, 회차·화자 수, 조건(4명 × 서로 다른 날 3회) 충족 여부, 뺀 것을 적음
    2. 화자마다 사용자 ID, 날 수, 날짜, 입력 장치를 한 줄로 적음
    3. 지표마다 단위, 화자 수, 값 수, 자유도, 평균, 측정 표준오차, 원 척도와 로그 척도의 최소 측정 가능 변화량, 표준편차 최대÷최소, 급내상관계수, 반영할 값을 한 줄로 적음. 보조 지표는 "보조(반영 안 함)", 측정 표준오차가 0인 지표는 "변동 없음(반영 안 함)"
    4. detail이면 회차마다 화자, 시작 시각(한국 시간), 세션 ID, 입력 장치, 지표 값을 한 줄로 적음. 평균 음량 변화 칸은 평가 문장 발화 음량(dBFS)임
    """
    grouped = days_by_speaker(visits)
    met = "충족" if protocol_met(visits) else "미충족"
    lines = [
        f"반복 측정: 최소 측정 가능 변화량 (서버 알고리즘 버전 {ALGORITHM_VERSION})",
        f"- 회차 {len(visits)}개, 화자 {len(grouped)}명. 조건({PROTOCOL_SPEAKERS}명 × 서로 다른 날 {PROTOCOL_DAYS}회) {met}",
        f"- 뺀 것: 개발용 회차 {excluded.dev_sessions}개, 뺀 화자의 회차 {excluded.excluded_speaker_sessions}개, "
        f"같은 날 뒤 회차 {excluded.later_same_day}개, 신뢰 낮음 값 {excluded.low_reliability_values}개",
        f"- {Metric.mean_level_change.value}의 평균과 회차별 값은 평가 문장 발화 음량(dBFS)",
        "",
    ]
    if not visits:
        return "\n".join([*lines, "계산할 회차가 없습니다."])

    lines += ["| 화자 | 사용자 ID | 날 수 | 날짜(한국 시간) | 입력 장치 |", "|---|---|---|---|---|"]
    for speaker, days in grouped.items():
        users = ", ".join(dict.fromkeys(str(visit.user_id)[:8] for visit in days))
        dates = ", ".join(f"{visit.started_at:%m/%d}" for visit in days)
        devices = ", ".join(dict.fromkeys(visit.input_device for visit in days))
        lines.append(f"| {str(speaker)[:8]} | {users} | {len(days)} | {dates} | {devices} |")

    chosen = {item.metric: item for item in selections}
    lines += [
        "",
        "| 지표 | 단위 | 화자 | 값 | 자유도 | 평균 | 측정 표준오차 | 최소 측정 가능 변화량(원 척도) | 표준편차 최대÷최소(원) "
        "| 최소 측정 가능 변화량(로그 척도) | 표준편차 최대÷최소(로그) | 급내상관계수 | 반영할 값 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        if not item.used_for_progress:
            applied = "보조(반영 안 함)"
        elif item.metric in chosen:
            pick = chosen[item.metric]
            applied = f"{pick.value:g}% (log)" if pick.scale is MdcScale.log else f"{pick.value:g} {item.unit} (raw)"
        elif item.sem_raw == 0:
            applied = "변동 없음(반영 안 함)"
        else:
            applied = "-"
        lines.append(
            f"| {item.metric} | {item.unit} | {item.speakers} | {item.values} | {item.degrees_of_freedom} | {number(item.mean)} | "
            f"{number(item.sem_raw)} | {number(item.mdc_raw)} | {number(item.sd_ratio_raw, 1)} | {number(item.mdc_log_pct, 1, '%')} | "
            f"{number(item.sd_ratio_log, 1)} | {number(item.icc, 2)} | {applied} |"
        )

    if detail:
        names = [metric.value for metric in METRICS]
        lines += ["", "| 화자 | 시작(한국 시간) | 세션 | 입력 장치 | " + " | ".join(names) + " |", "|---|---|---|---|" + "---|" * len(names)]
        for days in grouped.values():
            for visit in days:
                cells = " | ".join(number(visit.values.get(name)) for name in names)
                lines.append(f"| {str(visit.speaker)[:8]} | {visit.started_at:%Y-%m-%d %H:%M} | {visit.session_id} | {visit.input_device} | {cells} |")
    return "\n".join(lines)


def to_json(visits: list[Visit], results: list[MetricResult], selections: list[Selection], excluded: Excluded, detail: bool) -> str:
    """
    계산 결과를 JSON 문자열로 만듦
    입력: visits (계산에 쓴 회차), results (지표별 결과), selections (반영할 값), excluded (뺀 회차·값 수), detail (회차별 값도 넣을지)
    출력: JSON 문자열. algorithm_version, sessions(회차 수), protocol_met, excluded, speakers(화자마다 화자, 사용자 ID 목록, 날짜 목록, 입력 장치 목록), metrics(지표별 결과), selected(반영할 값), detail이면 visits(회차마다 화자, 사용자 ID, 세션 ID, 시작 시각(한국 시간 ISO 8601), 입력 장치, 값)
    알고리즘
    1. 화자별 요약, 지표별 결과, 반영할 값을 사전으로 바꿈
    2. detail이면 회차마다 ID와 시각을 문자열로 바꿔 넣음
    3. 한글을 그대로 두고 들여쓰기 2칸으로 직렬화함
    """
    data = {
        "algorithm_version": ALGORITHM_VERSION,
        "sessions": len(visits),
        "protocol_met": protocol_met(visits),
        "excluded": asdict(excluded),
        "speakers": [
            {
                "speaker": str(speaker),
                "user_ids": list(dict.fromkeys(str(visit.user_id) for visit in days)),
                "days": [visit.started_at.date().isoformat() for visit in days],
                "input_devices": list(dict.fromkeys(visit.input_device for visit in days)),
            }
            for speaker, days in days_by_speaker(visits).items()
        ],
        "metrics": [asdict(item) for item in results],
        "selected": [{**asdict(item), "scale": item.scale.value} for item in selections],
    }
    if detail:
        data["visits"] = [
            {
                "speaker": str(visit.speaker),
                "user_id": str(visit.user_id),
                "session_id": str(visit.session_id),
                "started_at": visit.started_at.isoformat(),
                "input_device": visit.input_device,
                "values": visit.values,
            }
            for days in days_by_speaker(visits).values()
            for visit in days
        ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None, db: Session | None = None, out: Callable[[str], None] = print) -> int:
    """
    반복 측정으로 최소 측정 가능 변화량을 계산하는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --same-speaker(한 화자로 묶을 사용자 ID, 여러 번), --exclude-speaker(뺄 화자의 사용자 ID, 여러 번), --log(로그 척도로 반영할 지표, 쉼표로 구분), --detail(회차별 값도 출력), --json(JSON으로 출력), --yes(mdc_values에 반영), --force(조건을 채우지 못해도 반영)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 출력했거나 반영함, 1 = 조건을 채우지 못했거나 반영할 값이 없어 반영하지 않음, 2 = 인자가 잘못됨). 결과를 out으로 씀. --yes일 때만 DB에 씀(apply_values)
    알고리즘
    1. 명령줄 인자를 읽고 사용자 ID 묶음을 화자 표로 바꿈(speaker_map). 잘못됐으면 알리고 2를 돌려줌
    2. db가 주어지지 않았을 때만 운영 접속 정보로 DB 세션을 만듦
    3. 회차를 읽고(load_visits) 지표별로 계산한 뒤(analyze) 반영할 값을 고름(select_values). --log가 잘못됐으면 알리고 2를 돌려줌
    4. --json이면 to_json으로, 아니면 render로 출력함
    5. --yes가 아니면 0을 돌려줌
    6. 반영할 값이 없거나, 조건(protocol_met)을 채우지 못했는데 --force가 없으면 이유를 알리고 1을 돌려줌
    7. 값을 넣고(apply_values) 넣은 지표 수를 알린 뒤 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="반복 측정으로 지표별 최소 측정 가능 변화량을 계산한다")
    parser.add_argument("--same-speaker", type=parse_group, action="append", default=[], metavar="사용자ID=사용자ID", help="두 사용자 ID를 한 화자로 묶는다")
    parser.add_argument("--exclude-speaker", type=uuid.UUID, action="append", default=[], metavar="사용자ID", help="이 화자의 회차를 모두 뺀다")
    parser.add_argument("--log", default="", metavar="지표,지표", help="로그 척도 값으로 반영할 지표. 나머지는 원 척도")
    parser.add_argument("--detail", action="store_true", help="회차별 값도 함께 보여 준다")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    parser.add_argument("--yes", action="store_true", help="진전도 지표의 값을 mdc_values에 넣는다. 없으면 계산 결과만 보여 준다")
    parser.add_argument("--force", action="store_true", help="화자·날 수 조건을 채우지 못해도 반영한다")
    args = parser.parse_args(argv)
    try:
        same_speaker = speaker_map(args.same_speaker)
    except ValueError as error:
        out(str(error))
        return 2

    if db is None:
        # 실제 실행에서만 운영 접속 정보를 읽는다.
        from app.db.engine import get_engine

        db = Session(get_engine())

    exclude = set(args.exclude_speaker)
    visits, excluded = load_visits(db, same_speaker, exclude)
    results = analyze(visits)
    try:
        removed = len({same_speaker.get(user_id, user_id) for user_id in exclude})
        selections = select_values(results, {name.strip() for name in args.log.split(",") if name.strip()}, removed)
    except ValueError as error:
        out(str(error))
        return 2
    out(to_json(visits, results, selections, excluded, args.detail) if args.json else render(visits, results, selections, excluded, args.detail))
    if not args.yes:
        return 0

    if not selections:
        out("반영할 값이 없습니다.")
        return 1
    if not protocol_met(visits) and not args.force:
        out(f"조건({PROTOCOL_SPEAKERS}명 × 서로 다른 날 {PROTOCOL_DAYS}회)을 채우지 못해 반영하지 않았습니다. 그래도 반영하려면 --force를 붙입니다.")
        return 1
    out(f"mdc_values에 지표 {apply_values(db, selections)}개의 값을 넣었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
