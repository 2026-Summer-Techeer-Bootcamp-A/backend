"""crud.search 토큰화 AND 매칭 회귀 테스트.

이전에는 쿼리 전체를 하나의 ILIKE 패턴으로 묶었기 때문에 "React backend"처럼 두 단어를
공백으로 붙여 검색하면, 그 문구가 제목에 그대로 들어있는 공고만 찾아 0건이 나왔다(단어
각각은 매치되는 공고가 있었는데도). 이 테스트는 공백 토큰화 + AND 매칭이 실제로
다중 토큰 쿼리를 살려내는지, 그리고 단일 토큰 쿼리는 기존과 동일하게 동작하는지 확인한다.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.crud.search import search_companies, search_postings
from app.models import Posting


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        s.add_all(
            [
                Posting(
                    source="t",
                    source_uid="1",
                    pool="domestic",
                    company="React Studio",
                    title="Backend Engineer",
                ),
                Posting(
                    source="t",
                    source_uid="2",
                    pool="domestic",
                    company="Kakao",
                    title="React Frontend Developer",
                ),
                Posting(
                    source="t",
                    source_uid="3",
                    pool="domestic",
                    company="Kakao",
                    title="Backend Developer",
                ),
            ]
        )
        s.commit()
        yield s
    engine.dispose()


def test_multi_word_query_ands_tokens_across_title_and_company(session: Session) -> None:
    # "React backend" 두 토큰 모두를 만족하는 공고는 회사가 "React Studio"이고 제목이
    # "Backend Engineer"인 첫 번째 공고뿐이다 — 토큰화 전에는 문구 전체(ILIKE
    # '%react backend%')가 제목/회사 어디에도 없어 0건이었다.
    results = search_postings(session, "React backend", limit=10)
    titles = [p.title for p in results]
    assert titles == ["Backend Engineer"]


def test_single_word_query_behaves_like_before(session: Session) -> None:
    results = search_postings(session, "Backend", limit=10)
    titles = {p.title for p in results}
    assert titles == {"Backend Engineer", "Backend Developer"}


def test_multi_word_query_with_no_common_posting_returns_empty(session: Session) -> None:
    # "Frontend backend" — 두 토큰을 동시에 만족하는 공고가 없어야 한다.
    results = search_postings(session, "Frontend backend", limit=10)
    assert results == []


def test_blank_query_returns_empty_list_not_match_all(session: Session) -> None:
    assert search_postings(session, "   ", limit=10) == []
    assert search_companies(session, "   ", limit=10) == []


def test_search_companies_multi_word_ands_tokens(session: Session) -> None:
    # company="Kakao"인 공고가 2건 있지만, 존재하지 않는 두 번째 토큰과 AND되면 0건이어야 한다.
    assert search_companies(session, "Kakao Nonexistent", limit=10) == []
    assert [c["company"] for c in search_companies(session, "Kakao", limit=10)] == ["Kakao"]
