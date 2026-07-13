"""jobkorea 소스 posting 행의 pool/region_country 일회성 보정.

배경: 구버전 ETL 버그로 source='jobkorea'(국내 취업 사이트) 행들이
pool='global', region_country=NULL 로 잘못 적재됐다. scripts/load_mart.py의
DOMESTIC_SOURCES는 이미 jobkorea를 포함하도록 고쳤지만, 그 스크립트는
TRUNCATE 후 전체 재적재라 이미 쌓인 데이터만 고치기엔 너무 파괴적/느리다.
그래서 기존 행만 targeted UPDATE로 바로잡는 별도의 일회성 스크립트.

멱등: 이미 pool='domestic', region_country='KR' 인 행은 WHERE 절에서
걸러져 재실행해도 갱신 건수는 0이 된다.

사용:
    DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb \
        python -m scripts.fix_source_pool
"""

from __future__ import annotations

import os
import sys

import psycopg

_UPDATE_SQL = """
UPDATE posting SET pool = 'domestic', region_country = 'KR'
WHERE source = 'jobkorea'
  AND (pool IS DISTINCT FROM 'domestic' OR region_country IS DISTINCT FROM 'KR')
"""


def fix_source_pool(conn: psycopg.Connection) -> int:
    """jobkorea 행을 pool='domestic', region_country='KR'로 보정하고 갱신된 행 수를 반환한다."""
    with conn.cursor() as cur:
        cur.execute(_UPDATE_SQL)
        updated = cur.rowcount
    conn.commit()
    return updated


def main() -> None:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(database_url) as conn:
        updated = fix_source_pool(conn)
    print(f"done, {updated} jobkorea rows fixed to pool='domestic', region_country='KR'", file=sys.stderr)


if __name__ == "__main__":
    main()
