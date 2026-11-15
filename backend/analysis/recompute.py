"""저장된 녹음으로 서버 지표를 지금 알고리즘 버전으로 다시 계산한다.

계산 방법이나 매개변수를 바꿔 알고리즘 버전을 올리면, 진전도는 같은 버전의 값끼리만 비교하므로 이미 끝난(done) 세션에는
새 버전 값이 없다. 이 도구가 S3의 녹음을 다시 읽어 지금 버전 값을 만든다. 이전 버전 값은 지우지 않고 그대로 둔다.
backend 폴더에서 실행한다. DB·S3는 .env(또는 환경 변수)의 운영 접속 정보를 쓴다.
    python -m analysis.recompute --all                모든 세션에서 다시 계산할 대상을 보여 준다(쓰지 않음)
    python -m analysis.recompute --user 사용자ID       그 사용자의 세션
    python -m analysis.recompute --session 세션ID      그 세션 하나
    --yes    실제로 다시 계산해 저장한다. 없으면 대상만 보여 준다
    --force  이미 지금 버전 값이 있는 세션과 서버 값이 없는 세션도 다시 계산한다

- 대상은 done 세션 중 서버 값이 이전 버전에만 있는 세션이다.
- 시작 시각이 이른 세션부터 계산한다. 평균 음량 변화의 기준 회차와 캘리브레이션이 앞선 세션에서 정해지기 때문이다.
- 같은 사용자·같은 목적의 앞선 세션에 이전 버전 값만 있으면 그 세션을 먼저 계산해야 한다.
  --session으로 뒤 세션만 고르면 계산하지 않고 알려 준다.
- 세션 하나가 실패하면(녹음을 읽지 못함 등) 그 세션은 그대로 두고 다음 세션으로 넘어간다. 세션 상태는 바꾸지 않는다.
  실패한 세션보다 뒤의 같은 사용자 세션은 평균 음량 변화의 기준 회차가 달라질 수 있다(측정값 세부 정보의 reference_session_id).
"""

import argparse
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MeasurementRow, MeasurementSource, SessionRow
from app.jobs.processing import calculate, save
from app.metrics.version import ALGORITHM_VERSION
from app.schemas.common import Purpose, SessionStatus
from app.storage.s3 import RecordingStorage

STALE = "이전 버전 값만 있음"
CURRENT = "이미 지금 버전 값이 있음"
EMPTY = "서버 값이 없음"
NOT_DONE = "done이 아님"
BLOCKED = "앞선 세션을 먼저 계산해야 함"
CHANGED = "계산하는 사이 상태가 바뀜"
SHOWN = 20  # 목록으로 보여 주는 세션 수. 넘으면 개수만 알린다
KST = timezone(timedelta(hours=9), "KST")  # 세션 시작 시각을 보여 주는 시간대


@dataclass(frozen=True)
class Item:
    session_id: uuid.UUID
    user_id: uuid.UUID
    purpose: Purpose
    started_at: datetime
    status: SessionStatus
    versions: tuple[str, ...]  # 서버 값이 있는 알고리즘 버전(정렬)

    @property
    def state(self) -> str:
        """
        세션이 다시 계산할 대상인지 나타내는 상태를 구함
        입력: 없음 (이 객체의 status, versions)
        출력: NOT_DONE, CURRENT, STALE, EMPTY 중 하나(문자열)
        알고리즘
        1. 상태가 done이 아니면 NOT_DONE
        2. 서버 값에 지금 알고리즘 버전(ALGORITHM_VERSION)이 있으면 CURRENT
        3. 다른 버전의 서버 값만 있으면 STALE, 서버 값이 하나도 없으면 EMPTY
        """
        if self.status is not SessionStatus.done:
            return NOT_DONE
        if ALGORITHM_VERSION in self.versions:
            return CURRENT
        return STALE if self.versions else EMPTY

    def describe(self) -> str:
        """
        세션을 한 줄로 나타냄
        입력: 없음
        출력: "세션 ID  사용자 ID  시작 시각 KST  목적  서버 값 버전" 형식의 문자열
        알고리즘
        1. 시작 시각은 한국 시간(UTC+9)으로 바꿔 분까지, 서버 값 버전은 쉼표로 이어 적음(없으면 "없음")
        """
        versions = ", ".join(self.versions) or "없음"
        return (
            f"{self.session_id}  사용자 {self.user_id}  {self.started_at.astimezone(KST):%Y-%m-%d %H:%M} KST  "
            f"{self.purpose.value}  서버 값 버전: {versions}"
        )


@dataclass
class Plan:
    targets: list[Item] = field(default_factory=list)
    skipped: dict[str, list[Item]] = field(default_factory=dict)  # 건너뛰는 이유 → 세션


@dataclass
class Outcome:
    done: int = 0  # 다시 계산해 저장한 세션 수
    values: int = 0  # 저장한 서버 지표 수
    changed: list[Item] = field(default_factory=list)  # 계산하는 사이 done이 아니게 된 세션
    failed: list[tuple[Item, str]] = field(default_factory=list)  # (세션, 실패 이유)


def load_items(db: Session, user_ids: list[uuid.UUID] | None) -> list[Item]:
    """
    세션과 그 세션에 서버 값이 있는 알고리즘 버전을 읽음. 읽기만 함
    입력: db (DB 세션), user_ids (이 사용자들의 세션만 읽음. None이면 모든 세션)
    출력: Item 목록. 시작 시각·세션 ID가 이른 순서
    알고리즘
    1. 서버 측정값(source = server)의 (세션 ID, 알고리즘 버전)을 겹치지 않게 읽어 세션별 버전 집합을 만듦
    2. 세션을 시작 시각·ID 순으로 읽어 버전 집합(정렬)과 함께 Item으로 만듦
    """
    sessions = select(SessionRow.id, SessionRow.user_id, SessionRow.purpose, SessionRow.started_at, SessionRow.status)
    versions = (
        select(MeasurementRow.session_id, MeasurementRow.algorithm_version)
        .join(SessionRow, SessionRow.id == MeasurementRow.session_id)
        .where(MeasurementRow.source == MeasurementSource.server)
        .distinct()
    )
    if user_ids is not None:
        sessions = sessions.where(SessionRow.user_id.in_(user_ids))
        versions = versions.where(SessionRow.user_id.in_(user_ids))
    by_session: dict[uuid.UUID, set[str]] = {}
    for session_id, version in db.execute(versions):
        by_session.setdefault(session_id, set()).add(version)
    return [
        Item(row.id, row.user_id, row.purpose, row.started_at, row.status, tuple(sorted(by_session.get(row.id, ()))))
        for row in db.execute(sessions.order_by(SessionRow.started_at, SessionRow.id))
    ]


def make_plan(items: list[Item], in_scope: Callable[[Item], bool], force: bool) -> Plan:
    """
    다시 계산할 세션과 건너뛸 세션을 나눔. 아무것도 쓰지 않음
    입력: items (고른 사용자들의 모든 세션, 시작 시각 순), in_scope (이번에 고른 세션인지 알려 주는 함수), force (지금 버전 값이 있거나 서버 값이 없는 done 세션도 다시 계산할지)
    출력: Plan(targets: 다시 계산할 세션(시작 시각 순), skipped: 건너뛰는 이유 → 세션 목록)
    알고리즘
    1. 고른 세션마다 상태를 봄. STALE이면 후보, force면 CURRENT·EMPTY도 후보, 그 밖은 그 상태를 이유로 건너뜀(NOT_DONE은 force여도 건너뜀)
    2. 후보마다, 같은 사용자·같은 목적이고 더 일찍 시작한 STALE 세션 중 후보가 아닌 것이 있으면 BLOCKED로 건너뜀. 그 세션을 두고 계산하면 평균 음량 변화의 기준 회차가 어긋나기 때문임
    3. 남은 후보를 targets로 둠
    """
    plan = Plan()
    candidates = []
    for item in items:
        if not in_scope(item):
            continue
        if item.state == STALE or (force and item.state in (CURRENT, EMPTY)):
            candidates.append(item)
        else:
            plan.skipped.setdefault(item.state, []).append(item)
    chosen = {item.session_id for item in candidates}
    waiting = [item for item in items if item.state == STALE and item.session_id not in chosen]
    for item in candidates:
        blocked = any(
            other.user_id == item.user_id and other.purpose is item.purpose and other.started_at < item.started_at
            for other in waiting
        )
        if blocked:
            plan.skipped.setdefault(BLOCKED, []).append(item)
        else:
            plan.targets.append(item)
    return plan


def recompute_one(db: Session, storage: RecordingStorage, session_id: uuid.UUID) -> int | None:
    """
    done 세션 하나의 서버 지표를 지금 알고리즘 버전으로 다시 계산해 저장함
    입력: db (DB 세션), storage (녹음 저장소, S3), session_id (세션 ID)
    출력: 저장한 서버 지표 수. 계산하는 사이 세션이 없어졌거나 done이 아니게 됐으면 아무것도 쓰지 않고 None. 녹음을 읽지 못하거나 계산이 실패하면 예외가 그대로 나감(되돌리기는 부르는 쪽이 함)
    알고리즘
    1. 세션을 DB 값으로 읽고, 처리 작업과 같은 방법으로 계산함(calculate. DB에는 쓰지 않음)
    2. 세션 행을 잠그고(FOR UPDATE) 다시 읽어 아직 done인지 확인함. 아니면 되돌리고 None을 돌려줌
    3. 처리 작업과 같은 방법으로 씀(save): 이 세션의 지금 버전 서버 값을 지우고 새 값을 넣으며, 평균 음량 변화와 사용자 캘리브레이션도 같은 규칙으로 정함. 다른 버전의 값은 건드리지 않음
    4. 커밋하고 지표 수를 돌려줌
    """
    session = db.get(SessionRow, session_id, populate_existing=True)
    if session is None:
        return None
    result, recomputed = calculate(db, storage, session)
    session = db.get(SessionRow, session_id, with_for_update=True, populate_existing=True)
    if session is None or session.status is not SessionStatus.done:
        db.rollback()
        return None
    count = save(db, session, result, recomputed)
    db.commit()
    return count


def apply_plan(db: Session, storage: RecordingStorage, plan: Plan, out: Callable[[str], None]) -> Outcome:
    """
    계획의 세션을 시작 시각 순서대로 다시 계산해 저장함
    입력: db (DB 세션), storage (녹음 저장소, S3), plan (make_plan이 만든 계획), out (진행 상황을 출력할 함수)
    출력: Outcome(다시 계산한 세션 수, 저장한 지표 수, 상태가 바뀌어 쓰지 않은 세션, 실패한 세션과 이유). 세션마다 DB에 쓰고 커밋함
    알고리즘
    1. targets를 차례로 recompute_one으로 계산함
    2. 예외가 나면 트랜잭션을 되돌리고 (세션, "예외 이름: 내용")을 failed에 넣은 뒤 다음 세션으로 넘어감. 그 세션의 기존 값과 상태는 그대로임
    3. None이 오면 changed에 넣음
    4. 세션마다 결과를 한 줄씩 출력함
    """
    outcome = Outcome()
    total = len(plan.targets)
    for index, item in enumerate(plan.targets, 1):
        try:
            count = recompute_one(db, storage, item.session_id)
        except Exception as error:  # 세션 하나의 실패가 나머지를 막지 않게 한다
            db.rollback()
            reason = f"{type(error).__name__}: {error}"
            outcome.failed.append((item, reason))
            out(f"  {index}/{total} 세션 {item.session_id}: 실패({reason})")
            continue
        if count is None:
            outcome.changed.append(item)
            out(f"  {index}/{total} 세션 {item.session_id}: 쓰지 않음({CHANGED})")
            continue
        outcome.done += 1
        outcome.values += count
        out(f"  {index}/{total} 세션 {item.session_id}: 지표 {count}개")
    return outcome


def render_plan(plan: Plan) -> list[str]:
    """
    계획을 사람이 읽는 줄로 만듦
    입력: plan (make_plan이 만든 계획)
    출력: 출력할 줄 목록. 첫 줄은 알고리즘 버전과 다시 계산할 세션 수, 이어서 대상 세션(SHOWN개까지), 건너뛰는 이유별 개수, BLOCKED 세션 목록(SHOWN개까지)
    알고리즘
    1. 대상 세션을 Item.describe로 한 줄씩 적고, SHOWN개를 넘으면 나머지는 개수만 적음
    2. 건너뛰는 이유마다 세션 수를 적음
    3. BLOCKED가 있으면 그 세션을 적고, --user로 그 사용자의 세션을 함께 계산하라고 알림
    """
    lines = [f"알고리즘 버전 {ALGORITHM_VERSION}: 다시 계산할 세션 {len(plan.targets)}개"]
    lines += [f"  {item.describe()}" for item in plan.targets[:SHOWN]]
    if len(plan.targets) > SHOWN:
        lines.append(f"  … 외 {len(plan.targets) - SHOWN}개")
    for reason, items in plan.skipped.items():
        lines.append(f"건너뜀({reason}): {len(items)}개")
    blocked = plan.skipped.get(BLOCKED, [])
    lines += [f"  {item.describe()}" for item in blocked[:SHOWN]]
    if blocked:
        lines.append("  같은 사용자·같은 목적의 앞선 세션에 이전 버전 값만 있습니다. --user로 그 사용자의 세션을 함께 계산하세요.")
    return lines


def main(
    argv: list[str] | None = None,
    db: Session | None = None,
    storage: RecordingStorage | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """
    저장된 녹음으로 서버 지표를 다시 계산하는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --all(모든 세션), --user(사용자 ID), --session(세션 ID) 중 하나는 꼭 줌. --yes(실제로 다시 계산해 저장, 없으면 대상만 보여 줌), --force(지금 버전 값이 있거나 서버 값이 없는 세션도 다시 계산)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - storage: 녹음 버킷 저장소(시험용 주입, 없으면 환경 변수·backend/.env의 AWS_REGION·AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·S3_BUCKET으로 만듦)
    - out: 결과를 출력할 함수(기본 print)
    출력: 종료 코드(0 = 미리 보기 또는 모두 다시 계산함, 1 = --yes인데 실패했거나 쓰지 못한 세션, 앞선 세션 때문에 계산하지 않은 세션이 있음, 2 = 세션이나 사용자를 찾지 못함). 계획과 결과를 out으로, 입력 오류를 표준 오류로 씀. --yes면 DB에 서버 측정값과 사용자 캘리브레이션을 씀
    알고리즘
    1. 명령줄 인자를 읽음
    2. db·storage가 주어지지 않았을 때만 운영 접속 정보로 DB 세션과 저장소를 만듦
    3. --session이면 그 세션의 사용자를, --user면 그 사용자를 정함. 세션이 없거나 그 사용자의 세션이 없으면 2를 돌려줌
    4. 그 사용자들(--all이면 모두)의 세션을 읽고(load_items) 계획을 세움(make_plan). --session이면 그 세션만 고른 것으로 봄
    5. 계획을 출력함(render_plan). --yes가 없으면 0을 돌려줌
    6. --yes면 계획대로 다시 계산하고(apply_plan) 결과 요약과 실패한 세션을 출력함
    7. 실패했거나 쓰지 못한 세션, BLOCKED 세션이 있으면 1, 없으면 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="저장된 녹음으로 서버 지표를 지금 알고리즘 버전으로 다시 계산한다")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="모든 세션")
    scope.add_argument("--user", type=uuid.UUID, help="사용자 ID")
    scope.add_argument("--session", type=uuid.UUID, help="세션 ID")
    parser.add_argument("--yes", action="store_true", help="실제로 다시 계산해 저장한다. 없으면 대상만 보여 준다")
    parser.add_argument("--force", action="store_true", help="지금 버전 값이 있거나 서버 값이 없는 세션도 다시 계산한다")
    args = parser.parse_args(argv)

    if db is None or storage is None:
        # 실제 실행에서만 운영 접속 정보를 읽는다.
        from app.db.engine import get_engine
        from app.storage.s3 import get_storage

        db = db or Session(get_engine())
        storage = storage or get_storage()

    user_ids = None
    if args.session is not None:
        session = db.get(SessionRow, args.session)
        if session is None:
            print("세션을 찾지 못했습니다", file=sys.stderr)
            return 2
        user_ids = [session.user_id]
    elif args.user is not None:
        user_ids = [args.user]
    items = load_items(db, user_ids)
    if args.user is not None and not items:
        print("그 사용자의 세션을 찾지 못했습니다", file=sys.stderr)
        return 2

    in_scope = (lambda item: item.session_id == args.session) if args.session is not None else (lambda item: True)
    plan = make_plan(items, in_scope, args.force)
    for line in render_plan(plan):
        out(line)
    if not args.yes:
        out("미리 보기입니다. 실제로 다시 계산하려면 --yes를 붙이세요.")
        return 0

    outcome = apply_plan(db, storage, plan, out)
    out(
        f"다시 계산했습니다: 세션 {outcome.done}개, 지표 {outcome.values}개. "
        f"실패 {len(outcome.failed)}개, 쓰지 않음 {len(outcome.changed)}개"
    )
    if outcome.failed:
        out("실패한 세션은 기존 값과 상태 그대로입니다. 같은 사용자의 뒤 세션은 평균 음량 변화의 기준 회차가 달라졌을 수 있습니다.")
    incomplete = outcome.failed or outcome.changed or plan.skipped.get(BLOCKED)
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
