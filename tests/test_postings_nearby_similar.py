"""GET /postings/{posting_id}/nearby, /postings/{posting_id}/similar 테스트."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Posting, PostingTech, Skill


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        java = Skill(canonical="Java", category="language")
        spring = Skill(canonical="Spring", category="framework")
        go = Skill(canonical="Go", category="language")
        seed.add_all([python, java, spring, go])
        seed.flush()

        gangnam_x = Posting(source="jumpit", source_uid="gx", pool="domestic", company="X", title="X",
                             post_date=date(2026, 7, 1), region_district="강남구")
        gangnam_y = Posting(source="jumpit", source_uid="gy", pool="domestic", company="Y", title="Y",
                             post_date=date(2026, 7, 2), region_district="강남구")
        mapo_z = Posting(source="jumpit", source_uid="mz", pool="domestic", company="Z", title="Z",
                          post_date=date(2026, 7, 1), region_district="마포구")
        seed.add_all([gangnam_x, gangnam_y, mapo_z])
        seed.commit()

        posting_a = Posting(source="jumpit", source_uid="sa", pool="domestic", company="A", title="A",
                             post_date=date(2026, 7, 1))
        posting_b = Posting(source="jumpit", source_uid="sb", pool="domestic", company="B", title="B",
                             post_date=date(2026, 7, 1))
        posting_c = Posting(source="jumpit", source_uid="sc", pool="domestic", company="C", title="C",
                             post_date=date(2026, 7, 1))
        posting_d = Posting(source="jumpit", source_uid="sd", pool="domestic", company="D", title="D",
                             post_date=date(2026, 7, 1))
        seed.add_all([posting_a, posting_b, posting_c, posting_d])
        seed.commit()

        seed.add_all(
            [
                PostingTech(posting_id=posting_a.id, skill_id=python.id),
                PostingTech(posting_id=posting_a.id, skill_id=java.id),
                PostingTech(posting_id=posting_a.id, skill_id=spring.id),
                PostingTech(posting_id=posting_b.id, skill_id=python.id),
                PostingTech(posting_id=posting_b.id, skill_id=java.id),
                PostingTech(posting_id=posting_c.id, skill_id=python.id),
                PostingTech(posting_id=posting_d.id, skill_id=go.id),
            ]
        )
        seed.commit()

        ids = {
            "gangnam_x": gangnam_x.id,
            "gangnam_y": gangnam_y.id,
            "mapo_z": mapo_z.id,
            "posting_a": posting_a.id,
            "posting_b": posting_b.id,
            "posting_c": posting_c.id,
            "posting_d": posting_d.id,
        }

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.ids = ids  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


# ---- nearby ----


def test_nearby_returns_same_district_excluding_self(client: TestClient) -> None:
    posting_id = client.ids["gangnam_x"]
    resp = client.get(f"/api/v1/postings/{posting_id}/nearby")
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert client.ids["gangnam_y"] in ids
    assert posting_id not in ids
    assert client.ids["mapo_z"] not in ids


def test_nearby_unknown_posting_404(client: TestClient) -> None:
    resp = client.get("/api/v1/postings/999999/nearby")
    assert resp.status_code == 404


# ---- similar ----


def test_similar_ordered_by_overlap_count(client: TestClient) -> None:
    posting_id = client.ids["posting_a"]
    resp = client.get(f"/api/v1/postings/{posting_id}/similar")
    assert resp.status_code == 200
    body = resp.json()
    items = body["items"]
    assert items[0]["id"] == client.ids["posting_b"]
    assert items[0]["overlap_count"] == 2
    assert items[1]["id"] == client.ids["posting_c"]
    assert items[1]["overlap_count"] == 1
    ids = [item["id"] for item in items]
    assert client.ids["posting_d"] not in ids  # overlap 0 excluded
    assert posting_id not in ids


def test_similar_unknown_posting_404(client: TestClient) -> None:
    resp = client.get("/api/v1/postings/999999/similar")
    assert resp.status_code == 404
