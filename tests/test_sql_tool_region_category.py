"""sql_tool.top_locations의 category 필터 통합 테스트 — 실 Postgres 필요.

top_locations는 category가 있으면 posting_category와 JOIN해 원시 SQL에 리터럴 ILIKE를
쓴다(다른 sql_tool 함수들의 category 필터와 동일한 패턴, app/services/rag/tools/sql_tool.py
의 _category_join 참고). SQLAlchemy의 Column.ilike()와 달리 text()에 박아 넣은 ILIKE
키워드는 sqlite 방언으로 컴파일되지 않아 sqlite에서는 문법 오류가 난다 — 그래서 이
검증은 fast tier(test_sql_tool_verbose.py)가 아니라 여기, 실 Postgres 통합 tier에 둔다.
DATABASE_URL 부재 시 conftest의 pytest_collection_modifyitems 훅에서 자동 skip된다.

버그 재현: 이 필터가 없던 시절에는 "백엔드 개발자 어디에 많이 몰려있어?"처럼
job_category가 추출된 질문에도 top_locations가 category를 완전히 무시하고 전체
지역 분포를 그대로 돌려줬다(실사용 중 "백엔드만 제한한 수치는 없다"고 답한 것으로
확인됨). 이 테스트는 category="백엔드"를 주면 실제로 백엔드 공고만 집계되는지 검증한다.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Posting, PostingCategory
from app.services.rag.tools import sql_tool

pytestmark = pytest.mark.integration


@pytest.fixture
def session(pg_conn: object) -> Iterator[Session]:
    """pg_conn으로 필요한 확장을 먼저 부트스트랩한 뒤 ORM 세션으로 시드/조회한다."""
    engine = create_engine(os.environ["DATABASE_URL"])
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        backend_posting = Posting(
            source="region-cat-test",
            source_uid="region-cat-backend",
            pool="domestic",
            title="백엔드 개발자",
            region_district="강남구",
        )
        frontend_posting = Posting(
            source="region-cat-test",
            source_uid="region-cat-frontend",
            pool="domestic",
            title="프론트 개발자",
            region_district="판교",
        )
        s.add_all([backend_posting, frontend_posting])
        s.flush()
        s.add_all(
            [
                PostingCategory(posting_id=backend_posting.id, category="서버/백엔드 개발자"),
                PostingCategory(posting_id=frontend_posting.id, category="프론트엔드 개발자"),
            ]
        )
        s.commit()
        try:
            yield s
        finally:
            s.rollback()
            s.query(PostingCategory).filter(
                PostingCategory.posting_id.in_([backend_posting.id, frontend_posting.id])
            ).delete(synchronize_session=False)
            s.query(Posting).filter(
                Posting.id.in_([backend_posting.id, frontend_posting.id])
            ).delete(synchronize_session=False)
            s.commit()
    engine.dispose()


def test_top_locations_with_category_filters_to_matching_postings(session: Session) -> None:
    # category 필터 전에는 top_locations가 category를 완전히 무시해 pool 전체 분포를
    # 그대로 돌려줬다(버그) — 필터를 걸면 표본 수(n)가 pool 전체보다 작아지고, 우리가
    # 심어둔 백엔드 공고의 지역(강남구)이 결과에 나타나야 한다. limit을 넉넉히 줘서
    # (실 DB에 이미 쌓인 데이터 때문에 상위 8위 밖으로 밀려나는 것을 방지) 존재 여부를
    # 확인한다.
    unfiltered = sql_tool.top_locations(session, pool="domestic")
    filtered = sql_tool.top_locations(session, pool="domestic", category="백엔드", limit=200)

    assert filtered["n"] < unfiltered["n"]
    filtered_names = {i["name"] for i in filtered["tool_result"]["items"]}
    assert "강남구" in filtered_names
