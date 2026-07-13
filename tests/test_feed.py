from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import (
    Posting,
    PostingCategory,
    PostingTech,
    RawPosting,
    Resume,
    ResumeSkill,
    Skill,
    User,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    today = date.today()

    with testing_session() as seed:
        python = Skill(canonical="python", category="language")
        react = Skill(canonical="react", category="framework")
        aws = Skill(canonical="aws", category="cloud")
        user = User(email="feed@example.com", password_hash="unused")
        seed.add_all([python, react, aws, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Resume", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, skill_id=python.id),
                ResumeSkill(resume_id=resume.resume_id, skill_id=react.id),
            ]
        )

        p1 = Posting(
            source="wanted",
            source_uid="p1",
            pool="domestic",
            company="p1 company",
            title="p1 title",
            industry="IT서비스",
            region_city="서울",
            post_date=today,
            close_date=today + timedelta(days=10),
        )
        p2 = Posting(
            source="wanted",
            source_uid="p2",
            pool="domestic",
            company="p2 company",
            title="p2 title",
            post_date=today - timedelta(days=1),
        )
        p3 = Posting(
            source="himalayas",
            source_uid="p3",
            pool="global",
            company="p3 company",
            title="p3 title",
            post_date=today - timedelta(days=3),
        )
        seed.add_all([p1, p2, p3])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=p1.id, category="백엔드"),
                PostingCategory(posting_id=p2.id, category="프론트엔드"),
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p1.id, skill_id=aws.id),
                PostingTech(posting_id=p2.id, skill_id=react.id),
                RawPosting(posting_id=p1.id, payload={"url": "https://example.com/p1"}),
                RawPosting(posting_id=p2.id, payload={"url": "https://example.com/p2"}),
                RawPosting(posting_id=p3.id, payload={"url": "https://example.com/p3"}),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_feed_anonymous_returns_cards_without_match(client):
    res = client.get("/api/v1/feed/postings", params={"page": 1, "page_size": 20})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    first = body["items"][0]
    assert first["title"] == "p1 title"  # post_date 내림차순
    assert first["industry"] == "IT서비스"
    assert first["region"] == "서울"
    assert first["categories"] == ["백엔드"]
    assert sorted(first["skills"]) == ["aws", "python"]
    assert first["match"] is None


def test_feed_authed_includes_match(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    res = client.get(
        "/api/v1/feed/postings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    first = res.json()["items"][0]  # p1: skills=[python, aws], 보유=[python, react]
    assert first["match"]["rate"] == 50.0
    assert first["match"]["owned_skills"] == ["python"]
    assert first["match"]["missing_skills"] == ["aws"]


def test_feed_posting_without_skills_has_null_match_when_authed(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    res = client.get("/api/v1/feed/postings", headers={"Authorization": f"Bearer {token}"})
    p3 = [i for i in res.json()["items"] if i["skills"] == []][0]
    assert p3["match"] is None


def test_feed_pool_filter(client):
    res = client.get("/api/v1/feed/postings", params={"pool": "global"})
    assert res.json()["total"] == 1


def test_feed_category_filter(client):
    res = client.get("/api/v1/feed/postings", params={"category": "백엔드"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["categories"] == ["백엔드"]


def test_feed_pagination(client):
    res = client.get("/api/v1/feed/postings", params={"page": 2, "page_size": 1})
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["page"] == 2
