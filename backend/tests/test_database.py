import psycopg


def test_connects_to_local_postgres(postgres_url):
    with psycopg.connect(postgres_url, connect_timeout=5) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
