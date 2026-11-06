"""시험용으로 만든 사용자·세션과 그 녹음 파일을 지운다.

ID 목록 파일에 적힌 것만 지운다. 삭제 API는 따로 없고, .env의 운영 접속 정보(DATABASE_URL, AWS)로 직접 지운다.
backend 폴더에서 실행한다.
    python -m tools.cleanup 목록.json          지울 대상만 보여준다(지우지 않음)
    python -m tools.cleanup 목록.json --yes    실제로 지운다

목록 파일 형식(JSON):
    {"user_ids": ["…"], "session_ids": ["…"], "s3_keys": ["users/…/….wav"]}
s3_keys는 목록에 있는 사용자의 저장 위치(users/{user_id}/) 안의 것만 받는다.
"""

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.models import RecordingRow, SessionRow, UserRow
from app.storage.s3 import RecordingStorage, user_prefix


@dataclass(frozen=True)
class Targets:
    user_ids: list[uuid.UUID]
    session_ids: list[uuid.UUID]
    s3_keys: list[str]


@dataclass
class Plan:
    user_ids: list[uuid.UUID] = field(default_factory=list)
    session_ids: list[uuid.UUID] = field(default_factory=list)
    recordings: int = 0
    s3_keys: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"사용자 {len(self.user_ids)}명, 세션 {len(self.session_ids)}개, "
            f"녹음 기록 {self.recordings}개, S3 객체 {len(self.s3_keys)}개"
        )


def load_targets(path: Path) -> Targets:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        user_ids = [uuid.UUID(value) for value in data.get("user_ids", [])]
        session_ids = [uuid.UUID(value) for value in data.get("session_ids", [])]
    except (TypeError, ValueError) as error:
        raise ValueError(f"UUID가 아닌 ID가 있습니다: {error}") from error
    s3_keys = list(data.get("s3_keys", []))
    if not (user_ids or session_ids):
        raise ValueError("지울 사용자나 세션 ID가 없습니다")
    prefixes = tuple(user_prefix(user_id) for user_id in user_ids)
    outside = [key for key in s3_keys if not key.startswith(prefixes)]
    if outside:
        raise ValueError(f"목록에 있는 사용자의 저장 위치 밖에 있는 S3 객체입니다: {', '.join(outside)}")
    return Targets(user_ids, session_ids, s3_keys)


def make_plan(db: Session, storage: RecordingStorage, targets: Targets) -> Plan:
    """DB와 S3에서 실제로 지울 대상을 찾는다. 아무것도 지우지 않는다."""
    user_ids = list(db.scalars(select(UserRow.id).where(UserRow.id.in_(targets.user_ids))))
    sessions = db.execute(
        select(SessionRow.id, SessionRow.user_id).where(
            or_(SessionRow.user_id.in_(user_ids), SessionRow.id.in_(targets.session_ids))
        )
    ).all()
    session_ids = [row.id for row in sessions]
    db_keys = list(db.scalars(select(RecordingRow.s3_key).where(RecordingRow.session_id.in_(session_ids))))

    # 목록 사용자의 저장 위치 전체와, 목록 세션의 저장 위치만 본다.
    keys = set(targets.s3_keys) | set(db_keys)
    for user_id in targets.user_ids:
        keys.update(storage.keys_under(user_prefix(user_id)))
    for row in sessions:
        if row.id in targets.session_ids:
            keys.update(storage.keys_under(f"{user_prefix(row.user_id)}sessions/{row.id}/"))
    return Plan(user_ids, session_ids, len(db_keys), sorted(keys))


def apply_plan(db: Session, storage: RecordingStorage, plan: Plan) -> None:
    # S3를 먼저 지운다. 실패하면 DB는 그대로라서 다시 실행할 수 있다.
    storage.delete(plan.s3_keys)
    # 사용자를 지우면 DB의 ON DELETE CASCADE로 세션·녹음·측정 기록·게임 기록이 함께 지워진다.
    db.execute(delete(SessionRow).where(SessionRow.id.in_(plan.session_ids)))
    db.execute(delete(UserRow).where(UserRow.id.in_(plan.user_ids)))
    db.commit()


def main(argv: list[str] | None = None, db: Session | None = None, storage: RecordingStorage | None = None) -> int:
    parser = argparse.ArgumentParser(description="시험용으로 만든 사용자·세션과 녹음 파일을 지운다")
    parser.add_argument("targets", type=Path, help="지울 ID 목록 파일(JSON)")
    parser.add_argument("--yes", action="store_true", help="실제로 지운다. 없으면 대상만 보여준다")
    args = parser.parse_args(argv)

    try:
        targets = load_targets(args.targets)
    except (OSError, ValueError) as error:
        print(f"목록 파일을 쓸 수 없습니다: {error}", file=sys.stderr)
        return 2

    if db is None or storage is None:
        # 실제 실행에서만 운영 접속 정보를 읽는다.
        from app.db.engine import get_engine
        from app.storage.s3 import get_storage

        db = db or Session(get_engine())
        storage = storage or get_storage()

    plan = make_plan(db, storage, targets)
    if not args.yes:
        print(f"미리 보기: {plan.summary()}를 지웁니다. 실제로 지우려면 --yes를 붙이세요.")
        for key in plan.s3_keys[:10]:
            print(f"  {key}")
        if len(plan.s3_keys) > 10:
            print(f"  … 외 {len(plan.s3_keys) - 10}개")
        return 0

    apply_plan(db, storage, plan)
    print(f"지웠습니다: {plan.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
