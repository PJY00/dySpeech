import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.schemas.common import Decision, MdcScale, Purpose, RecordingKind, SessionStatus

# 제약 이름을 정해 두어야 마이그레이션에서 이름으로 찾고 지울 수 있다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Timestamp = DateTime(timezone=True)
EMPTY_JSON = text("'{}'::jsonb")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class MeasurementSource(StrEnum):
    device = "device"
    server = "server"


def str_enum(enum: type[StrEnum], name: str) -> Enum:
    """PostgreSQL 자체 enum 타입 대신 문자열과 CHECK 제약으로 둔다. 값이 늘 때 마이그레이션이 단순하다."""
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def children(back_populates: str):
    """하위 행. 지울 때는 DB의 ON DELETE CASCADE에 맡겨, 하위 행을 불러오지 않는다."""
    return relationship(back_populates=back_populates, cascade="all, delete-orphan", passive_deletes=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # 앱이 사용자를 만든 시각(요청 값)
    created_at: Mapped[datetime] = mapped_column(Timestamp)

    sessions: Mapped[list["SessionRow"]] = children("user")


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[Purpose] = mapped_column(str_enum(Purpose, "purpose"))
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    ended_at: Mapped[datetime | None] = mapped_column(Timestamp)
    device_model: Mapped[str] = mapped_column(String(100))
    os_version: Mapped[str] = mapped_column(String(100))
    app_version: Mapped[str] = mapped_column(String(100))
    input_device: Mapped[str] = mapped_column(String(100))
    sample_rate: Mapped[int]
    status: Mapped[SessionStatus] = mapped_column(
        str_enum(SessionStatus, "status"), server_default=SessionStatus.open.value
    )
    is_baseline: Mapped[bool] = mapped_column(server_default=false())
    # 처리에 실패했을 때 {"code", "message"}
    error: Mapped[dict | None] = mapped_column(JSONB)
    client_meta: Mapped[dict] = mapped_column(JSONB, server_default=EMPTY_JSON)
    complete_client_meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    user: Mapped[UserRow] = relationship(back_populates="sessions")
    recordings: Mapped[list["RecordingRow"]] = children("session")
    measurements: Mapped[list["MeasurementRow"]] = children("session")
    game_runs: Mapped[list["GameRunRow"]] = children("session")


class RecordingRow(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[RecordingKind] = mapped_column(str_enum(RecordingKind, "kind"))
    trial_no: Mapped[int | None]
    sentence_id: Mapped[str | None] = mapped_column(String(64))
    # 게임 기록은 complete 때 들어오므로 외래 키를 두지 않는다.
    game_run_id: Mapped[uuid.UUID | None]
    agc_signal: Mapped[str | None] = mapped_column(String(32))
    s3_key: Mapped[str] = mapped_column(String(255), unique=True)
    duration_s: Mapped[float]
    sample_rate: Mapped[int]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    # complete 때 S3에서 파일을 확인한 시각과 보관 기한. 보관 기한은 S3 수명 주기 규칙처럼 S3에 올라간 시각부터 센다.
    uploaded_at: Mapped[datetime | None] = mapped_column(Timestamp)
    retain_until: Mapped[datetime | None] = mapped_column(Timestamp)

    session: Mapped[SessionRow] = relationship(back_populates="recordings")


class MeasurementRow(Base):
    __tablename__ = "measurements"
    # 기기 값은 algorithm_version이 없으므로, 없는 값끼리도 같은 것으로 보고 겹침을 막는다.
    __table_args__ = (
        UniqueConstraint("session_id", "metric", "source", "algorithm_version", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float]
    unit: Mapped[str] = mapped_column(String(16))
    source: Mapped[MeasurementSource] = mapped_column(str_enum(MeasurementSource, "source"))
    algorithm_version: Mapped[str | None] = mapped_column(String(32))
    low_reliability: Mapped[bool] = mapped_column(server_default=false())
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    session: Mapped[SessionRow] = relationship(back_populates="measurements")


class GameRunRow(Base):
    __tablename__ = "game_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    game: Mapped[str] = mapped_column(String(64))
    level: Mapped[int]
    round_index: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    params: Mapped[dict] = mapped_column(JSONB, server_default=EMPTY_JSON)
    success_rate_pct: Mapped[float]
    decision: Mapped[Decision] = mapped_column(str_enum(Decision, "decision"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONB, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    session: Mapped[SessionRow] = relationship(back_populates="game_runs")


class MdcValueRow(Base):
    __tablename__ = "mdc_values"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float]
    scale: Mapped[MdcScale] = mapped_column(str_enum(MdcScale, "scale"))
    method: Mapped[str] = mapped_column(String(64))
    n_speakers: Mapped[int]
    n_repeats: Mapped[int]
    algorithm_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    active: Mapped[bool] = mapped_column(server_default=false())
    note: Mapped[str | None] = mapped_column(Text)
