from collections.abc import Iterator

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
        "as_of": "2026-07-08",
        "sample_size": 2,
    }
