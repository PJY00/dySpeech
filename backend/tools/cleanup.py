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
        """
        지울 대상의 개수를 한 줄 문장으로 나타냄
        입력: 없음
        출력: "사용자 N명, 세션 N개, 녹음 기록 N개, S3 객체 N개" 형식의 문자열
        알고리즘
        1. 사용자·세션 ID 수, 녹음 기록 수, S3 키 수를 문장에 넣어 돌려줌
        """
        return (
            f"사용자 {len(self.user_ids)}명, 세션 {len(self.session_ids)}개, "
            f"녹음 기록 {self.recordings}개, S3 객체 {len(self.s3_keys)}개"
        )


def load_targets(path: Path) -> Targets:
    """
    지울 ID 목록 파일(JSON)을 읽어 검사함
    입력: path (목록 파일 경로. 형식 {"user_ids": […], "session_ids": […], "s3_keys": […]}, 모두 선택)
    출력: 지울 대상(Targets: user_ids, session_ids, s3_keys). UUID가 아닌 ID가 있거나, 사용자·세션 ID가 하나도 없거나, 목록 사용자의 저장 위치(users/{user_id}/) 밖의 S3 키가 있으면 ValueError. 파일을 읽지 못하면 OSError, JSON이 아니면 JSONDecodeError(ValueError)
    알고리즘
    1. 파일을 UTF-8로 읽어 JSON으로 풂
    2. user_ids·session_ids를 UUID로 바꾸고, 바꾸지 못하면 ValueError를 냄
    3. 사용자 ID와 세션 ID가 모두 비어 있으면 ValueError를 냄
    4. s3_keys 중 목록 사용자의 저장 위치로 시작하지 않는 키가 있으면 그 키들을 적어 ValueError를 냄
    5. 세 목록을 Targets로 묶어 돌려줌
    """
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
    """
    DB와 S3에서 실제로 지울 대상을 찾음. 아무것도 지우지 않음
    입력: db (DB 세션), storage (녹음 버킷 저장소), targets (load_targets로 읽은 지울 대상)
    출력: 지울 계획(Plan: DB에 있는 사용자 ID, 지울 세션 ID, 그 세션들의 녹음 기록 수, 지울 S3 키 목록(정렬))
    알고리즘
    1. 목록 사용자 중 DB에 있는 사용자 ID만 고름
    2. 그 사용자들의 세션과 목록에 적힌 세션을 함께 찾아 세션 ID 목록을 만듦
    3. 그 세션들의 녹음 행에 적힌 S3 키를 읽음
    4. 지울 S3 키를 모음: 목록의 s3_keys, DB 녹음 키, 목록 사용자 저장 위치(users/{user_id}/) 아래 모든 객체, 목록 세션의 저장 위치(users/{user_id}/sessions/{session_id}/) 아래 객체
    5. 모은 키를 정렬해 Plan으로 돌려줌
    """
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
    """
    지울 계획대로 S3 객체를 먼저 지우고 DB 행을 지움
    입력: db (DB 세션), storage (녹음 버킷 저장소), plan (make_plan이 만든 지울 계획)
    출력: 없음. S3 객체를 지우고, DB의 세션·사용자 행을 지운 뒤 커밋함. S3 삭제가 하나라도 실패하면 RuntimeError(이때 DB는 그대로)
    알고리즘
    1. S3 객체를 먼저 지움. 실패하면 DB는 그대로라서 다시 실행할 수 있음
    2. 계획의 세션 행을 지움
    3. 계획의 사용자 행을 지움. DB의 ON DELETE CASCADE로 세션·녹음·측정 기록·게임 기록이 함께 지워짐
    4. 커밋함
    """
    # S3를 먼저 지운다. 실패하면 DB는 그대로라서 다시 실행할 수 있다.
    storage.delete(plan.s3_keys)
    # 사용자를 지우면 DB의 ON DELETE CASCADE로 세션·녹음·측정 기록·게임 기록이 함께 지워진다.
    db.execute(delete(SessionRow).where(SessionRow.id.in_(plan.session_ids)))
    db.execute(delete(UserRow).where(UserRow.id.in_(plan.user_ids)))
    db.commit()


def main(argv: list[str] | None = None, db: Session | None = None, storage: RecordingStorage | None = None) -> int:
    """
    시험용 사용자·세션과 녹음 파일을 지우는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). targets(지울 ID 목록 파일, JSON), --yes(실제로 지움, 없으면 대상만 보여줌)
    - db: DB 세션(시험용 주입, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결)
    - storage: 녹음 버킷 저장소(시험용 주입, 없으면 환경 변수·backend/.env의 AWS_REGION·AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·S3_BUCKET으로 만듦)
    출력: 종료 코드(0 = 미리 보기 또는 삭제 완료, 2 = 목록 파일을 읽지 못하거나 내용이 틀림). 표준 출력에 미리 보기나 삭제 결과, 표준 오류에 목록 파일 오류를 씀. --yes면 S3 객체와 DB 행을 지움
    알고리즘
    1. 명령줄 인자를 읽음
    2. 목록 파일을 load_targets로 읽고, OSError·ValueError가 나면 표준 오류에 이유를 쓰고 2를 돌려줌
    3. db·storage가 주어지지 않았을 때만 운영 접속 정보로 DB 세션과 저장소를 만듦
    4. make_plan으로 지울 대상을 찾음
    5. --yes가 없으면 개수 요약과 S3 키 앞 10개(나머지는 개수만)를 출력하고 0을 돌려줌
    6. --yes면 apply_plan으로 지우고 요약을 출력한 뒤 0을 돌려줌
    """
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
