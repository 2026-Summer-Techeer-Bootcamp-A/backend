from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Cert, Posting, PostingCategory, PostingCert, Resume, ResumeCert


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
        aws = Cert(name="AWS Certified Developer")
        pmp = Cert(name="PMP")
        seed.add_all([aws, pmp])
        seed.commit()

        resume = Resume(user_id=1, title="Backend Resume", position="Developer", pool="domestic")
        seed.add(resume)
        seed.commit()

        seed.add(ResumeCert(resume_id=resume.resume_id, cert_id=aws.id, is_out_of_dict=False))

        posting_a = Posting(
            source="wanted",
            source_uid="wanted-1",
            pool="domestic",
            title="Backend Developer A",
        )
        posting_b = Posting(
            source="jumpit",
            source_uid="jumpit-1",
            pool="domestic",
            title="Backend Developer B",
        )
        seed.add_all([posting_a, posting_b])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=posting_a.id, category="Developer"),
                PostingCategory(posting_id=posting_b.id, category="Developer"),
                PostingCert(posting_id=posting_a.id, cert_id=aws.id),
                PostingCert(posting_id=posting_b.id, cert_id=pmp.id),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_cert_gap_by_resume_id(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?resume_id=1&pool=domestic&position=Developer")

    assert response.status_code == 200
    assert response.json() == {
        "required": [
            {"name": "AWS Certified Developer", "share": 0.5, "posting_count": 1},
            {"name": "PMP", "share": 0.5, "posting_count": 1},
        ],
        "owned": [{"name": "AWS Certified Developer"}],
        "gap": [{"name": "PMP", "share": 0.5}],
        "as_of": date.today().isoformat(),
        "sample_size": 2,
    }


def test_get_cert_gap_returns_404_for_unknown_resume(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?resume_id=999&pool=domestic&position=Developer")

    assert response.status_code == 404
    assert response.json() == {"detail": "resume not found"}


def test_get_cert_gap_rejects_invalid_pool(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?resume_id=1&pool=invalid&position=Developer")

    assert response.status_code == 422


def test_get_cert_gap_returns_empty_gap_when_no_matching_postings(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?resume_id=1&pool=global&position=Developer")

    assert response.status_code == 200
    assert response.json() == {
        "required": [],
        "owned": [{"name": "AWS Certified Developer"}],
        "gap": [],
        "as_of": date.today().isoformat(),
        "sample_size": 0,
    }


def test_get_cert_gap_requires_resume_id_or_session_id(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?pool=domestic&position=Developer")

    assert response.status_code == 400
    assert response.json() == {"detail": "resume_id or session_id is required"}


def test_get_cert_gap_uses_empty_owned_for_existing_session_id(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from app.core import redis as redis_module

    class FakeRedis:
        def exists(self, key: str) -> int:
            return 1 if key == "resume_confirm:guest-1" else 0

    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())

    response = client.get("/api/v1/cert/gap?session_id=guest-1&pool=domestic&position=Developer")

    assert response.status_code == 200
    assert response.json() == {
        "required": [
            {"name": "AWS Certified Developer", "share": 0.5, "posting_count": 1},
            {"name": "PMP", "share": 0.5, "posting_count": 1},
        ],
        "owned": [],
        "gap": [
            {"name": "AWS Certified Developer", "share": 0.5},
            {"name": "PMP", "share": 0.5},
        ],
        "as_of": date.today().isoformat(),
        "sample_size": 2,
    }


def test_get_cert_gap_returns_404_for_unknown_session_id(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from app.core import redis as redis_module

    class FakeRedis:
        def exists(self, key: str) -> int:
            return 0

    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())

    response = client.get("/api/v1/cert/gap?session_id=missing&pool=domestic&position=Developer")

    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_get_cert_gap_allows_missing_position(client: TestClient) -> None:
    response = client.get("/api/v1/cert/gap?resume_id=1&pool=domestic")

    assert response.status_code == 200
    assert response.json()["sample_size"] == 2
