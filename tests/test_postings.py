from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import Cert, Posting, PostingCategory, PostingCert, PostingTech, RawPosting, Resume, ResumeSkill, Skill, User


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
        python = Skill(canonical="Python", category="language")
        spring = Skill(canonical="Spring", category="framework")
        aws = Skill(canonical="AWS", category="cloud")
        aws_saa = Cert(name="AWS SAA")
        user = User(email="postings@example.com", password_hash="unused")
        seed.add_all([python, spring, aws, aws_saa, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Backend Resume", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, skill_id=python.id),
                ResumeSkill(resume_id=resume.resume_id, skill_id=aws.id),
            ]
        )

        older = Posting(
            source="wanted",
            source_uid="wanted-1",
            pool="domestic",
            company="Toss",
            title="Backend Engineer",
            post_date=date(2026, 7, 1),
            close_date=date(2026, 7, 31),
            career_min=3,
            career_max=5,
            region_city="Seoul",
            industry="fintech",
            response_rate=0.82,
            lat=37.566500,
            lng=126.978000,
        )
        newer = Posting(
            source="jumpit",
            source_uid="jumpit-1",
            pool="domestic",
            company="Kakao",
            title="Backend Platform Engineer",
            post_date=date(2026, 7, 5),
            close_date=date(2026, 7, 20),
        )
        frontend = Posting(
            source="wanted",
            source_uid="wanted-2",
            pool="domestic",
            company="Naver",
            title="Frontend Engineer",
            post_date=date(2026, 7, 7),
            close_date=date(2026, 8, 10),
        )
        global_posting = Posting(
            source="himalayas",
            source_uid="himalayas-1",
            pool="global",
            company="Stripe",
            title="Remote Backend Engineer",
            post_date=date(2026, 7, 4),
        )
        seed.add_all([older, newer, frontend, global_posting])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=older.id, category="backend"),
                PostingCategory(posting_id=newer.id, category="backend"),
                PostingCategory(posting_id=frontend.id, category="frontend"),
                PostingCategory(posting_id=global_posting.id, category="backend"),
                PostingTech(posting_id=older.id, skill_id=python.id),
                PostingTech(posting_id=older.id, skill_id=spring.id),
                PostingTech(posting_id=newer.id, skill_id=spring.id),
                PostingTech(posting_id=frontend.id, skill_id=aws.id),
                PostingCert(posting_id=older.id, cert_id=aws_saa.id),
                RawPosting(posting_id=older.id, payload={"url": "https://example.com/wanted-1"}),
                RawPosting(posting_id=newer.id, payload={"link": "https://example.com/jumpit-1"}),
                RawPosting(posting_id=frontend.id, payload={"source_url": "https://example.com/wanted-2"}),
                RawPosting(posting_id=global_posting.id, payload={"url": "https://example.com/himalayas-1"}),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_postings_returns_filtered_latest_cards(client: TestClient) -> None:
    response = client.get("/api/v1/postings?pool=domestic&position=backend")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": 2,
                "title": "Backend Platform Engineer",
                "company": "Kakao",
                "post_date": "2026-07-05",
                "close_date": "2026-07-20",
                "skills": ["Spring"],
                "url": "https://example.com/jumpit-1",
            },
            {
                "id": 1,
                "title": "Backend Engineer",
                "company": "Toss",
                "post_date": "2026-07-01",
                "close_date": "2026-07-31",
                "skills": ["Python", "Spring"],
                "url": "https://example.com/wanted-1",
            },
        ],
        "page": 1,
        "page_size": 20,
        "total": 2,
        "as_of": date.today().isoformat(),
    }


def test_get_postings_sorts_domestic_by_deadline(client: TestClient) -> None:
    response = client.get("/api/v1/postings?pool=domestic&position=backend&sort=deadline")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2, 1]


def test_get_postings_rejects_global_deadline_sort(client: TestClient) -> None:
    response = client.get("/api/v1/postings?pool=global&sort=deadline")

    assert response.status_code == 422


def test_get_postings_match_only_filters_and_adds_matched_count(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)

    response = client.get(
        "/api/v1/postings?pool=domestic&position=backend&match_only=true&resume_id=1",
        headers={"Authorization": f"Bearer {create_access_token(1)}"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == 1
    assert response.json()["items"][0]["matched_count"] == 1


def test_get_postings_match_only_requires_resume_id(client: TestClient) -> None:
    response = client.get("/api/v1/postings?pool=domestic&match_only=true")

    assert response.status_code == 422


def test_get_postings_paginates_after_filtering_and_sorting(client: TestClient) -> None:
    response = client.get("/api/v1/postings?pool=domestic&page=2&page_size=1")

    assert response.status_code == 200
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 1
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 1


def test_get_posting_detail_returns_full_posting(client: TestClient) -> None:
    response = client.get("/api/v1/postings/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "source": "wanted",
        "pool": "domestic",
        "company": "Toss",
        "title": "Backend Engineer",
        "post_date": "2026-07-01",
        "close_date": "2026-07-31",
        "career_min": 3,
        "career_max": 5,
        "region": "Seoul",
        "lat": 37.5665,
        "lng": 126.978,
        "industry": "fintech",
        "response_rate": 0.82,
        "categories": ["backend"],
        "skills": ["Python", "Spring"],
        "certs": ["AWS SAA"],
        "url": "https://example.com/wanted-1",
        "desc_sections": [],
    }


def test_get_posting_detail_returns_404_for_missing_posting(client: TestClient) -> None:
    response = client.get("/api/v1/postings/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "posting not found"}


def test_get_posting_detail_returns_lat_lng_for_domestic_posting(client: TestClient) -> None:
    response = client.get("/api/v1/postings/1")

    assert response.status_code == 200
    assert response.json()["lat"] == 37.5665
    assert response.json()["lng"] == 126.978


def test_get_posting_detail_returns_none_lat_lng_when_missing(client: TestClient) -> None:
    response = client.get("/api/v1/postings/2")

    assert response.status_code == 200
    assert response.json().get("lat") is None
    assert response.json().get("lng") is None
