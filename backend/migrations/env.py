"""Alembic 실행 설정.

마이그레이션 대상 DB를 꼭 밝혀야 한다. backend 폴더에서 실행한다.
    alembic -x db=test upgrade head   로컬 테스트 DB(TEST_DATABASE_URL)
    alembic -x db=prod upgrade head   운영 DB(DATABASE_URL)
.env에 운영 연결 문자열도 함께 있으므로, 대상을 밝히지 않으면 실행하지 않는다.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.engine import sqlalchemy_url
from app.db.models import Base
from app.settings import Settings

config = context.config

# 테스트에서 부를 때는 로그 설정을 건드리지 않는다(pytest의 로그 수집이 끊기지 않게).
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

TARGETS = {"test": "test_database_url", "prod": "database_url"}


def database_url() -> str:
    # 테스트는 주소를 직접 넘긴다.
    url = config.attributes.get("database_url")
    if url:
        return url
    target = context.get_x_argument(as_dictionary=True).get("db")
    if target not in TARGETS:
        raise SystemExit(
            "마이그레이션 대상을 밝혀야 합니다: alembic -x db=test …(로컬 테스트 DB) 또는 alembic -x db=prod …(운영 DB)"
        )
    value = getattr(Settings(), TARGETS[target])
    if value is None:
        raise SystemExit(f"{TARGETS[target].upper()}가 설정되지 않았습니다")
    return value.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url(database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(database_url()), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
