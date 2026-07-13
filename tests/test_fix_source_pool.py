"""fix_source_pool 통합 테스트 — DATABASE_URL이 가리키는 실제 Postgres에 대해 동작 검증.

주의: fix_source_pool()의 UPDATE는 source_uid로 좁혀지지 않고 source='jobkorea'
전체에 걸리므로, 실제 posting 테이블에 바로 테스트 행을 넣으면 실운영/개발 DB에
이미 쌓여있는 진짜 jobkorea 행까지 같이 갱신-커밋해버린다. 그래서 각 테스트는
세션 로컬 TEMP TABLE로 "posting"이라는 이름 자체를 가려(search_path에서 pg_temp가
우선순위를 가짐) 실제 posting 테이블은 전혀 건드리지 않는다. 커넥션이 닫히면
임시 테이블은 자동으로 사라지므로 별도 정리도 필요 없다.
"""

import os

import psycopg
import pytest

from scripts.fix_source_pool import fix_source_pool

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a live Postgres (set DATABASE_URL)"
)


@pytest.fixture
def conn():
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cur:
            # 실제 posting 테이블과 동일한 컬럼/제약을 가진 세션 전용 임시 테이블로 가린다.
            cur.execute(
                "CREATE TEMP TABLE posting "
                "(LIKE posting INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)"
            )
        connection.commit()
        yield connection
        # 커넥션 종료 시 TEMP TABLE은 자동 drop됨 — 실제 posting 테이블은 손대지 않았음.


def _insert(conn: psycopg.Connection, row_id: int, source_uid: str, source: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO posting (id, source, source_uid, title, pool, region_country)
            VALUES (%s, %s, %s, 'title', 'global', NULL)
            """,
            (row_id, source, source_uid),
        )
    conn.commit()


def test_fixes_jobkorea_rows_to_domestic_kr(conn):
    _insert(conn, -1, "jk-1", "jobkorea")

    updated = fix_source_pool(conn)

    assert updated == 1
    with conn.cursor() as cur:
        cur.execute("SELECT pool, region_country FROM posting WHERE id = -1")
        pool, region_country = cur.fetchone()
    assert pool == "domestic"
    assert region_country == "KR"


def test_leaves_other_sources_untouched(conn):
    _insert(conn, -1, "jumpit-1", "jumpit")

    fix_source_pool(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT pool, region_country FROM posting WHERE id = -1")
        pool, region_country = cur.fetchone()
    assert pool == "global"
    assert region_country is None


def test_second_run_is_a_noop(conn):
    _insert(conn, -1, "jk-idem", "jobkorea")

    first_run_updated = fix_source_pool(conn)
    second_run_updated = fix_source_pool(conn)

    assert first_run_updated == 1
    assert second_run_updated == 0
