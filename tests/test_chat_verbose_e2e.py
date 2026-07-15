from collections.abc import Iterator

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
        react = Skill(canonical="React", category="frontend", is_ambiguous=False)
        seed.add(react)
        seed.flush()
        p1 = Posting(source="t", source_uid="1", pool="domestic", title="프론트 개발자")
        seed.add(p1)
        seed.flush()
        seed.add(PostingTech(posting_id=p1.id, skill_id=react.id))
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chat_default_has_no_debug_on_results(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"question": "React 수요 어때?"})
    assert response.status_code == 200
    body = response.json()
    assert all(r.get("debug") is None for r in body["tool_results"])


def test_chat_verbose_true_attaches_debug(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"question": "React 수요 어때?", "verbose": True})
    assert response.status_code == 200
    body = response.json()
    assert any(r.get("debug") is not None for r in body["tool_results"])
