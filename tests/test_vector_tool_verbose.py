"""vector_tool.semantic_search verbose=True 통합 테스트 — 실 Postgres(pgvector) 필요.

DATABASE_URL 부재 시 conftest의 pytest_collection_modifyitems 훅에서 자동 skip된다.
embed_query는 고정 벡터로 monkeypatch해 실 BGE-M3 모델 로딩 없이 SQL/디버그 페이로드
경로만 검증한다.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Posting, PostingEmbedding
from app.services.rag.tools import vector_tool

pytestmark = pytest.mark.integration

FAKE_VEC = [0.1] * 1024


@pytest.fixture
def session(pg_conn: object) -> Iterator[Session]:
    """pg_conn으로 vector/citext 확장을 먼저 부트스트랩한 뒤 ORM 세션으로 시드/정리."""
    engine = create_engine(os.environ["DATABASE_URL"])
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        posting = Posting(
            source="verbose-test",
            source_uid="verbose-1",
            pool="domestic",
            title="테스트 공고",
            company="테스트컴퍼니",
        )
        s.add(posting)
        s.flush()
        s.add(PostingEmbedding(id=posting.id, embedding=FAKE_VEC, model="fake"))
        s.commit()
        posting_id = posting.id
        try:
            yield s
        finally:
            s.execute(text("DELETE FROM posting_embedding WHERE id = :id"), {"id": posting_id})
            s.execute(text("DELETE FROM posting WHERE id = :id"), {"id": posting_id})
            s.commit()
    engine.dispose()


def test_semantic_search_verbose_false_has_no_debug(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vector_tool, "embed_query", lambda q: FAKE_VEC)
    result = vector_tool.semantic_search(session, "테스트 쿼리", pool="domestic", verbose=False)
    assert result is not None
    assert result["tool_result"]["debug"] is None


def test_semantic_search_verbose_true_exposes_embedding_and_sql(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vector_tool, "embed_query", lambda q: FAKE_VEC)
    result = vector_tool.semantic_search(session, "테스트 쿼리", pool="domestic", verbose=True)
    assert result is not None
    debug = result["tool_result"]["debug"]
    assert debug["embedding_dim"] == 1024
    assert debug["embedding_preview"] == [0.1] * 8
    assert "posting_embedding" in debug["sql"]
    assert "<=>" in debug["sql"]
