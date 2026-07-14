"""SQLite가 못 잡는 실 Postgres 기능 검증 (pgvector, CITEXT).

각 테스트는 세션 로컬 TEMP TABLE에서만 쓰므로 실 테이블을 건드리지 않는다.
"""

import pytest

pytestmark = pytest.mark.integration


def test_pgvector_orders_by_l2_distance(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE emb (id int, v vector(3))")
        cur.execute("INSERT INTO emb VALUES (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0.9,0.1,0]')")
        pg_conn.commit()
        cur.execute("SELECT id FROM emb ORDER BY v <-> '[1,0,0]' LIMIT 2")
        nearest = [row[0] for row in cur.fetchall()]
    assert nearest == [1, 3]


def test_citext_unique_is_case_insensitive(pg_conn):
    import psycopg

    with pg_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE u (email citext UNIQUE)")
        cur.execute("INSERT INTO u VALUES ('User@Example.com')")
        pg_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("INSERT INTO u VALUES ('user@example.com')")
        pg_conn.commit()
    pg_conn.rollback()
