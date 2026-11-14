"""같은 세션의 기기 값과 서버 값을 지표별로 비교한 표를 만든다.

앱은 측정 블록에서 기기가 계산한 값(device_measurements)을 complete 때 함께 보내고, 서버는 같은 녹음에서 공식 값을 계산한다.
게임의 난이도 기준값은 기기 값으로 정해지므로, 두 값이 얼마나 다른지 앱 팀에 알려 준다.
backend 폴더에서 실행한다. DB는 .env(또는 환경 변수)의 운영 접속 정보로 읽기만 한다. S3는 쓰지 않는다.
    python -m analysis.device_vs_server                     모든 done 세션
    python -m analysis.device_vs_server --user 사용자ID       그 사용자의 세션
    python -m analysis.device_vs_server --session 세션ID      그 세션 하나
    --purpose training|retest    그 목적의 세션만
    --detail                     세션별 값도 함께 보여 준다
    --include-dev                개발용 사용자의 세션(client_meta의 dev가 true)도 넣는다
    --include-low-reliability    기기나 서버가 "신뢰 낮음"으로 표시한 값도 넣는다
    --json                       JSON으로 출력한다(기본은 마크다운 표)

- 비교 대상: done 세션에서 같은 지표의 기기 값과 지금 알고리즘 버전의 서버 값이 둘 다 있는 쌍
- d(차이) = x_device(기기 값) − x_server(서버 값). 양수면 기기 값이 더 크다.
- 95% 일치 한계 = d̄(평균 차이) ± 1.96 × s(차이의 표본 표준편차). 같은 사람의 여러 회차가 섞이면 실제보다 좁게 나올 수 있다.
- 평균 상대 차이(%) = 100 × (d(차이) ÷ x_server(서버 값))의 평균. 비율로 볼 수 있는 단위(초, 음절/초, Hz)에만 낸다.
"""

import argparse
import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.config.metrics import METRICS
from app.db.models import MeasurementRow, MeasurementSource, SessionRow
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Metric, Purpose, SessionStatus, Unit

LIMIT_FACTOR = 1.96  # 95% 일치 한계의 배수
RATIO_UNITS = {Unit.seconds, Unit.syllables_per_second, Unit.hertz}  # 상대 차이(%)를 내는 단위
METRIC_ORDER = {metric.value: index for index, metric in enumerate(METRICS)}


@dataclass(frozen=True)
class Pair:
    session_id: uuid.UUID
    user_id: uuid.UUID
    purpose: str
    started_at: datetime
    metric: str
    unit: str
    device: float
    server: float
    low_reliability: bool  # 기기나 서버 값 중 하나라도 신뢰 낮음

    @property
    def difference(self) -> float:
        """
        기기 값과 서버 값의 차이를 구함
        입력: 없음 (이 객체의 device, server)
        출력: d(차이) = x_device(기기 값) − x_server(서버 값). 단위는 그 지표의 단위
        알고리즘
        1. 기기 값에서 서버 값을 뺌
        """
        return self.device - self.server


@dataclass(frozen=True)
class Excluded:
    dev_sessions: int  # 개발용 사용자의 세션이라 뺀 세션 수
    low_reliability_pairs: int  # 신뢰 낮음이라 뺀 쌍 수


@dataclass(frozen=True)
class Summary:
    metric: str
    unit: str
    pairs: int
    speakers: int
    mean_difference: float
    sd_difference: float | None  # 쌍이 2개보다 적으면 None
    mean_absolute_difference: float
    max_absolute_difference: float
    limits_of_agreement: tuple[float, float] | None  # 쌍이 2개보다 적으면 None
    mean_relative_difference_pct: float | None  # 비율로 볼 수 없는 단위면 None


def load_pairs(
    db: Session,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    purpose: Purpose | None = None,
    include_dev: bool = False,
    include_low_reliability: bool = False,
) -> tuple[list[Pair], Excluded]:
    """
    같은 세션·같은 지표의 기기 값과 서버 값 쌍을 읽음. 읽기만 함
    입력:
    - db: DB 세션
    - user_id: 이 사용자의 세션만(선택)
    - session_id: 이 세션만(선택)
    - purpose: 이 목적의 세션만(선택)
    - include_dev: 개발용 사용자의 세션도 넣을지
    - include_low_reliability: 신뢰 낮음 값도 넣을지
    출력: (Pair 목록(세션 시작 시각·세션 ID·지표 정의 순), Excluded(뺀 개발용 세션 수, 뺀 신뢰 낮음 쌍 수))
    알고리즘
    1. done 세션에서 기기 측정값(source = device)과, 같은 세션·같은 지표이고 알고리즘 버전이 지금 버전(ALGORITHM_VERSION)인 서버 측정값(source = server)을 짝지어 읽음
    2. 사용자·세션·목적 조건이 있으면 그 세션만 읽음
    3. 세션 등록이나 complete의 client_meta에 dev가 true인 세션은 include_dev가 아니면 빼고 세션 수를 셈
    4. 기기나 서버 값이 신뢰 낮음인 쌍은 include_low_reliability가 아니면 빼고 쌍 수를 셈
    5. 남은 쌍을 시작 시각, 세션 ID, 지표 정의(METRICS) 순으로 정렬해 돌려줌
    """
    device = aliased(MeasurementRow)
    server = aliased(MeasurementRow)
    query = (
        select(
            SessionRow.id, SessionRow.user_id, SessionRow.purpose, SessionRow.started_at,
            SessionRow.client_meta, SessionRow.complete_client_meta,
            device.metric, server.unit, device.value, server.value,
            device.low_reliability, server.low_reliability,
        )
        .join(device, and_(device.session_id == SessionRow.id, device.source == MeasurementSource.device))
        .join(
            server,
            and_(
                server.session_id == SessionRow.id,
                server.metric == device.metric,
                server.source == MeasurementSource.server,
                server.algorithm_version == ALGORITHM_VERSION,
            ),
        )
        .where(SessionRow.status == SessionStatus.done)
    )
    if user_id is not None:
        query = query.where(SessionRow.user_id == user_id)
    if session_id is not None:
        query = query.where(SessionRow.id == session_id)
    if purpose is not None:
        query = query.where(SessionRow.purpose == purpose)

    pairs: list[Pair] = []
    dev_sessions: set[uuid.UUID] = set()
    low_pairs = 0
    for row in db.execute(query):
        (found_id, found_user, found_purpose, started_at, meta, complete_meta, metric, unit, device_value, server_value, device_low, server_low) = row
        if not include_dev and ((meta or {}).get("dev") is True or (complete_meta or {}).get("dev") is True):
            dev_sessions.add(found_id)
            continue
        low = bool(device_low or server_low)
        if low and not include_low_reliability:
            low_pairs += 1
            continue
        pairs.append(Pair(found_id, found_user, found_purpose.value, started_at, metric, unit, device_value, server_value, low))
    pairs.sort(key=lambda pair: (pair.started_at, str(pair.session_id), METRIC_ORDER.get(pair.metric, len(METRIC_ORDER))))
    return pairs, Excluded(len(dev_sessions), low_pairs)


def summarize(pairs: list[Pair]) -> list[Summary]:
    """
    지표별로 기기 값과 서버 값의 차이를 요약함
    입력: pairs (load_pairs가 읽은 쌍)
    출력: Summary 목록(지표 정의 순). 지표마다 쌍 수, 화자 수, 평균 차이, 차이의 표준편차, 평균 절대 차이, 최대 절대 차이, 95% 일치 한계, 평균 상대 차이(%)
    알고리즘
    1. 쌍을 지표별로 묶고 d(차이) = x_device(기기 값) − x_server(서버 값)를 구함
    2. d̄(평균 차이), 평균 절대 차이, 최대 절대 차이를 구함
    3. 쌍이 2개 이상이면 s(차이의 표본 표준편차, n − 1로 나눔)와 95% 일치 한계 d̄ ± 1.96 × s를 구함. 아니면 둘 다 None
    4. 단위가 초, 음절/초, Hz이면 서버 값이 0이 아닌 쌍으로 평균 상대 차이(%) = 100 × (d ÷ x_server)의 평균을 구함. 그런 쌍이 없거나 다른 단위면 None
    """
    by_metric: dict[str, list[Pair]] = {}
    for pair in pairs:
        by_metric.setdefault(pair.metric, []).append(pair)
    summaries = []
    for metric in sorted(by_metric, key=lambda name: METRIC_ORDER.get(name, len(METRIC_ORDER))):
        group = by_metric[metric]
        differences = np.array([pair.difference for pair in group])
        mean = float(differences.mean())
        sd = float(differences.std(ddof=1)) if len(group) >= 2 else None
        limits = (mean - LIMIT_FACTOR * sd, mean + LIMIT_FACTOR * sd) if sd is not None else None
        relative = None
        if group[0].unit in {unit.value for unit in RATIO_UNITS}:
            ratios = [pair.difference / pair.server for pair in group if pair.server != 0]
            relative = float(100 * np.mean(ratios)) if ratios else None
        summaries.append(
            Summary(
                metric=metric,
                unit=group[0].unit,
                pairs=len(group),
                speakers=len({pair.user_id for pair in group}),
                mean_difference=mean,
                sd_difference=sd,
                mean_absolute_difference=float(np.abs(differences).mean()),
                max_absolute_difference=float(np.abs(differences).max()),
                limits_of_agreement=limits,
                mean_relative_difference_pct=relative,
            )
        )
    return summaries


def signed(value: float | None) -> str:
    """
    차이 값을 부호를 붙여 적음
    입력: value (값 또는 None)
    출력: 소수 둘째 자리까지, 양수면 "+"를 붙인 문자열. None이면 "-"
    알고리즘
    1. None이면 "-", 아니면 부호와 소수 둘째 자리로 적음
    """
    return "-" if value is None else f"{value:+.2f}"


def render(pairs: list[Pair], summaries: list[Summary], excluded: Excluded, detail: bool) -> str:
    """
    비교 결과를 마크다운 표로 만듦
    입력: pairs (비교한 쌍), summaries (지표별 요약), excluded (뺀 세션·쌍 수), detail (세션별 값 표도 넣을지)
    출력: 여러 줄 문자열. 머리말(알고리즘 버전, 세션 수, 화자 수, 뺀 것), 지표별 요약 표, detail이면 세션별 값 표. 쌍이 없으면 머리말과 "비교할 값이 없습니다" 한 줄
    알고리즘
    1. 머리말에 알고리즘 버전, 세션 수, 화자 수, 차이의 정의, 뺀 개발용 세션 수와 신뢰 낮음 쌍 수를 적음
    2. 쌍이 없으면 "비교할 값이 없습니다"를 적고 끝냄
    3. 지표마다 단위, 쌍 수, 화자 수, 평균 차이, 표준편차, 평균 절대 차이, 최대 절대 차이, 95% 일치 한계, 평균 상대 차이를 한 줄로 적음. 없는 값은 "-"
    4. detail이면 쌍마다 세션 ID, 시작 시각, 목적, 지표, 기기 값, 서버 값, 차이를 한 줄로 적고, 신뢰 낮음이면 표시함
    """
    sessions = {pair.session_id for pair in pairs}
    speakers = {pair.user_id for pair in pairs}
    lines = [
        f"기기 값과 서버 값 비교 (서버 알고리즘 버전 {ALGORITHM_VERSION})",
        f"- 세션 {len(sessions)}개, 화자 {len(speakers)}명",
        "- 차이 = 기기 값 − 서버 값. 양수면 기기 값이 더 큼",
        f"- 뺀 것: 개발용 세션 {excluded.dev_sessions}개, 신뢰 낮음 쌍 {excluded.low_reliability_pairs}개",
        "",
    ]
    if not pairs:
        return "\n".join([*lines, "비교할 값이 없습니다."])
    lines += [
        "| 지표 | 단위 | 쌍 | 화자 | 평균 차이 | 차이의 표준편차 | 평균 절대 차이 | 최대 절대 차이 | 95% 일치 한계 | 평균 상대 차이 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in summaries:
        limits = "-" if item.limits_of_agreement is None else f"{signed(item.limits_of_agreement[0])} ~ {signed(item.limits_of_agreement[1])}"
        relative = "-" if item.mean_relative_difference_pct is None else f"{item.mean_relative_difference_pct:+.1f}%"
        sd = "-" if item.sd_difference is None else f"{item.sd_difference:.2f}"
        lines.append(
            f"| {item.metric} | {item.unit} | {item.pairs} | {item.speakers} | {signed(item.mean_difference)} | {sd} | "
            f"{item.mean_absolute_difference:.2f} | {item.max_absolute_difference:.2f} | {limits} | {relative} |"
        )
    if detail:
        lines += ["", "| 세션 | 시작 | 목적 | 지표 | 기기 값 | 서버 값 | 차이 |", "|---|---|---|---|---|---|---|"]
        for pair in pairs:
            mark = " (신뢰 낮음)" if pair.low_reliability else ""
            lines.append(
                f"| {pair.session_id} | {pair.started_at:%Y-%m-%d %H:%M} | {pair.purpose} | {pair.metric} | "
                f"{pair.device:.2f} | {pair.server:.2f} | {signed(pair.difference)}{mark} |"
            )
    return "\n".join(lines)


def to_json(pairs: list[Pair], summaries: list[Summary], excluded: Excluded, detail: bool) -> str:
    """
    비교 결과를 JSON 문자열로 만듦
    입력: pairs (비교한 쌍), summaries (지표별 요약), excluded (뺀 세션·쌍 수), detail (세션별 값도 넣을지)
    출력: JSON 문자열. algorithm_version, sessions(세션 수), speakers(화자 수), excluded, metrics(지표별 요약), detail이면 pairs(쌍마다 세션 ID, 사용자 ID, 목적, 시작 시각, 지표, 단위, 기기 값, 서버 값, 차이, 신뢰 낮음)
    알고리즘
    1. 요약과 뺀 수를 사전으로 바꿈
    2. detail이면 쌍마다 ID와 시각을 문자열로 바꾸고 차이를 더해 넣음
    3. 한글을 그대로 두고 들여쓰기 2칸으로 직렬화함
    """
    data = {
        "algorithm_version": ALGORITHM_VERSION,
        "sessions": len({pair.session_id for pair in pairs}),
        "speakers": len({pair.user_id for pair in pairs}),
        "excluded": asdict(excluded),
        "metrics": [asdict(item) for item in summaries],
    }
    if detail:
        data["pairs"] = [
            {
                **asdict(pair),
                "session_id": str(pair.session_id),
                "user_id": str(pair.user_id),
                "started_at": pair.started_at.isoformat(),
                "difference": pair.difference,
            }
            for pair in pairs
        ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None, db: Session | None = None, out: Callable[[str], None] = print) -> int:
    """
    기기 값과 서버 값을 비교하는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --user(사용자 ID)와 --session(세션 ID)은 둘 중 하나만, --purpose(training 또는 retest), --detail(세션별 값도 출력), --include-dev(개발용 세션 포함), --include-low-reliability(신뢰 낮음 값 포함), --json(JSON으로 출력)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 출력함. 비교할 값이 없어도 0). 결과를 out으로 씀. DB는 읽기만 함
    알고리즘
    1. 명령줄 인자를 읽음
    2. db가 주어지지 않았을 때만 운영 접속 정보로 DB 세션을 만듦
    3. 조건에 맞는 쌍을 읽고(load_pairs) 지표별로 요약함(summarize)
    4. --json이면 to_json으로, 아니면 render로 출력하고 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="같은 세션의 기기 값과 서버 값을 지표별로 비교한다")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--user", type=uuid.UUID, help="사용자 ID")
    scope.add_argument("--session", type=uuid.UUID, help="세션 ID")
    parser.add_argument("--purpose", type=Purpose, choices=[Purpose.training, Purpose.retest], help="이 목적의 세션만")
    parser.add_argument("--detail", action="store_true", help="세션별 값도 함께 보여 준다")
    parser.add_argument("--include-dev", action="store_true", help="개발용 사용자의 세션도 넣는다")
    parser.add_argument("--include-low-reliability", action="store_true", help='"신뢰 낮음" 값도 넣는다')
    parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    args = parser.parse_args(argv)

    if db is None:
        # 실제 실행에서만 운영 접속 정보를 읽는다.
        from app.db.engine import get_engine

        db = Session(get_engine())

    pairs, excluded = load_pairs(db, args.user, args.session, args.purpose, args.include_dev, args.include_low_reliability)
    summaries = summarize(pairs)
    out(to_json(pairs, summaries, excluded, args.detail) if args.json else render(pairs, summaries, excluded, args.detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
