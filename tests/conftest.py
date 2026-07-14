"""공용 테스트 픽스처 + fast/slow 게이팅.

DATABASE_URL이 없으면 @pytest.mark.integration 테스트를 전부 skip 한다
(로컬 개발자는 실 Postgres 없이 fast tier만 돌린다).
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - Base에 모든 모델을 등록
from app.core.db import Base


def pytest_collection_modifyitems(config, items):
    """DATABASE_URL 부재 시 integration 마커 테스트를 일괄 skip."""
    if os.environ.get("DATABASE_URL"):
        return
    skip_integration = pytest.mark.skip(
        reason="requires a live Postgres (set DATABASE_URL)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """인메모리 SQLite 엔진 + 전체 스키마 (fast tier 공용)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_conn() -> Iterator["object"]:
    """실 Postgres psycopg 커넥션 (통합 tier 공용). 확장 부트스트랩 포함.

    DATABASE_URL 부재 시 이 픽스처를 쓰는 테스트는 collection 훅에서 이미 skip 된다.
    """
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE EXTENSION IF NOT EXISTS citext")
        conn.commit()
        yield conn
