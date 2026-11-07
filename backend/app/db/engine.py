from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.settings import get_settings


def sqlalchemy_url(url: str) -> str:
    """Supabase가 주는 postgresql:// 연결 문자열을 psycopg 드라이버 표기로 바꾼다."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str) -> Engine:
    # Supabase Session pooler는 연결 수가 제한되므로 풀을 작게 둔다.
    return create_engine(
        sqlalchemy_url(url),
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다")
    return make_engine(url.get_secret_value())
