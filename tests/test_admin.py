from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import User


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
        admin = User(email="admin@example.com", password_hash="hash", is_admin=True)
        normal = User(email="normal@example.com", password_hash="hash", is_admin=False)
        seed.add_all([admin, normal])
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_admin_endpoints_require_authentication(client: TestClient) -> None:
    # 1. Get status without auth -> 401
    resp = client.get("/api/v1/admin/collector/status")
    assert resp.status_code == 401

    # 2. Trigger run without auth -> 401
    resp = client.post("/api/v1/admin/collector/run", json={"source": "wanted"})
    assert resp.status_code == 401


def test_admin_endpoints_reject_non_admin(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)

    # User ID 2 is the normal user
    token = create_access_token(2)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/v1/admin/collector/status", headers=headers)
    assert resp.status_code == 403

    resp = client.post("/api/v1/admin/collector/run", json={"source": "wanted"}, headers=headers)
    assert resp.status_code == 403


def test_admin_status_and_run_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    # run_collector_job은 실 Postgres(SessionLocal)와 collector 서브프로세스에 직접 붙는 백그라운드 잡이라,
    # TestClient가 동기로 실행하면 테스트 DB를 못 탄다. 202 트리거 계약만 검증하도록 잡 자체를 목킹한다.
    monkeypatch.setattr("app.routers.admin.run_collector_job", lambda *args, **kwargs: None)

    # User ID 1 is the admin
    token = create_access_token(1)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Get status when no runs have occurred
    resp = client.get("/api/v1/admin/collector/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sources"]) == 5
    for s in data["sources"]:
        assert s["last_run_at"] is None
        assert s["ingested_count"] == 0
        assert s["error"] is None

    # 2. Trigger a run
    resp = client.post("/api/v1/admin/collector/run", json={"source": "wanted", "limit": 2}, headers=headers)
    assert resp.status_code == 202
    run_data = resp.json()
    assert "job_id" in run_data
    assert run_data["sources"] == ["wanted"]
