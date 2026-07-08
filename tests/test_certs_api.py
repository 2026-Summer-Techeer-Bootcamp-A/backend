from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Cert


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
                Cert(name="AWS Certified Solutions Architect"),
                Cert(name="AWS Certified Developer"),
                Cert(name="정보처리기사"),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_search_certs_by_query(client: TestClient) -> None:
    response = client.get("/api/v1/certs?q=aws")

    assert response.status_code == 200
    assert response.json() == {
        "certs": [
            {"name": "AWS Certified Developer"},
            {"name": "AWS Certified Solutions Architect"},
        ]
    }
