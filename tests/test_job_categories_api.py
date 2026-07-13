from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import JobCategory, Posting, PostingCategory


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as seed:
        seed.add_all(
            [
                JobCategory(name="marketing", is_tech=False),
                JobCategory(name="backend", is_tech=True),
                JobCategory(name="frontend", is_tech=True),
                JobCategory(name="deleted", is_tech=True, is_deleted=True),
            ]
        )
        seed.commit()

        # pool 스코핑 관찰용: backend는 국내 공고에만, frontend는 해외 공고에만
        # 실제로 태깅돼 있고, marketing은 어느 pool에도 태깅된 공고가 없다.
        domestic_posting = Posting(
            source="jumpit", source_uid="d1", pool="domestic", title="Backend Engineer"
        )
        global_posting = Posting(
            source="himalayas", source_uid="g1", pool="global", title="Frontend Engineer"
        )
        seed.add_all([domestic_posting, global_posting])
        seed.commit()
        seed.add_all(
            [
                PostingCategory(posting_id=domestic_posting.id, category="backend"),
                PostingCategory(posting_id=global_posting.id, category="frontend"),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_job_categories_returns_canonical_categories(client: TestClient) -> None:
    response = client.get("/api/v1/job-categories")

    assert response.status_code == 200
    assert response.json() == {
        "categories": [
            {"name": "backend", "is_tech": True},
            {"name": "frontend", "is_tech": True},
            {"name": "marketing", "is_tech": False},
        ]
    }


def test_get_job_categories_scoped_to_domestic_pool(client: TestClient) -> None:
    response = client.get("/api/v1/job-categories", params={"pool": "domestic"})

    assert response.status_code == 200
    assert response.json() == {"categories": [{"name": "backend", "is_tech": True}]}


def test_get_job_categories_scoped_to_global_pool(client: TestClient) -> None:
    response = client.get("/api/v1/job-categories", params={"pool": "global"})

    assert response.status_code == 200
    assert response.json() == {"categories": [{"name": "frontend", "is_tech": True}]}
