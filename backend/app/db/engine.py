from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.settings import get_settings


def sqlalchemy_url(url: str) -> str:
    """
    Supabase가 주는 postgresql:// 연결 문자열을 psycopg 드라이버 표기로 바꿈
    입력: url (PostgreSQL 연결 문자열)
    출력: SQLAlchemy 연결 문자열. postgresql:// 또는 postgres://로 시작하면 postgresql+psycopg://로 바꾸고, 아니면 그대로 돌려줌
    알고리즘
    1. 두 접두사를 차례로 보고, 맞는 것이 있으면 그 부분을 postgresql+psycopg://로 바꿔 돌려줌
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str) -> Engine:
    """
    연결 문자열로 작은 연결 풀을 쓰는 DB 엔진을 만들어 돌려줌
    입력: url (PostgreSQL 연결 문자열)
    출력: SQLAlchemy 엔진 (기본 연결 3개, 추가 연결 2개까지, 쓰기 전 연결 확인, 연결 제한 시간 10초)
    알고리즘
    1. sqlalchemy_url로 드라이버 표기를 바꾸고 엔진을 만듦. Supabase Session pooler는 연결 수가 제한되므로 풀을 작게 둠
    """
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
    """
    설정의 DATABASE_URL로 DB 엔진을 한 번만 만들어 다시 씀
    입력: 없음 (환경 변수 DATABASE_URL, app/settings.py)
    출력: SQLAlchemy 엔진(lru_cache로 하나만 둠). DATABASE_URL이 없으면 RuntimeError
    알고리즘
    1. 설정에서 database_url을 읽어, 없으면 RuntimeError("DATABASE_URL이 설정되지 않았습니다")를 일으킴
    2. make_engine으로 엔진을 만듦. 처음 부를 때만 만들고 이후에는 같은 엔진을 돌려줌
    """
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다")
    return make_engine(url.get_secret_value())
