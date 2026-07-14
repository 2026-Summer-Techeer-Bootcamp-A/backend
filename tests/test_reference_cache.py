"""참조 데이터 Redis 캐시(reference_cache) + 네 엔드포인트 캐싱 동작 테스트.

Redis/DB 없이 도는 fast tier 테스트. Redis는 FakeRedis로 주입하고, 각
라우터의 CRUD 호출은 빈 결과로 패치해 캐시 히트/미스 경로만 검증한다.
"""

from collections.abc import Iterator

import json

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.schemas.skill import SkillListResponse
from app.services import reference_cache


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.setex_calls.append((key, ttl, value))


class BrokenRedis:
    def get(self, key: str) -> str | None:
        raise RedisError("redis unavailable")

    def setex(self, key: str, ttl: int, value: str) -> None:
        raise RedisError("redis unavailable")


# ---------------------------------------------------------------------------
# 1. reference_cache 유닛 테스트 — 키 구분 / 라운드트립 / TTL / fail-open
# ---------------------------------------------------------------------------


def test_cache_key_distinguishes_namespace_and_params() -> None:
    # 같은 파라미터라도 엔드포인트(namespace)가 다르면 다른 키
    assert reference_cache.make_reference_cache_key(
        "skills", {"q": "py"}
    ) != reference_cache.make_reference_cache_key("certs", {"q": "py"})
    # 파라미터 값이 다르면 다른 키
    assert reference_cache.make_reference_cache_key(
        "skills", {"q": "py", "limit": 20}
    ) != reference_cache.make_reference_cache_key("skills", {"q": "py", "limit": 10})
    # 동일 namespace + 동일 파라미터는 항상 같은 키(키 순서 무관)
    assert reference_cache.make_reference_cache_key(
        "skills", {"q": "py", "limit": 20}
    ) == reference_cache.make_reference_cache_key("skills", {"limit": 20, "q": "py"})


def test_round_trip_preserves_schema_and_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(reference_cache, "redis_client", fake)

    payload = SkillListResponse(skills=[])
    key = reference_cache.make_reference_cache_key("skills", {"q": None})
    reference_cache.set_cached(key, payload, ttl_seconds=1234)

    cached = reference_cache.get_cached(key, SkillListResponse)
    assert cached == payload
    assert fake.setex_calls[0][1] == 1234  # set_cached가 받은 TTL을 그대로 setex에 전달


def test_invalid_cached_payload_is_treated_as_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    key = reference_cache.make_reference_cache_key("skills", {"q": None})
    fake.store[key] = json.dumps({"wrong": "shape"})
    monkeypatch.setattr(reference_cache, "redis_client", fake)

    # 스키마가 안 맞는 캐시는 잘못된 응답으로 내보내지 않고 미스로 취급
    assert reference_cache.get_cached(key, SkillListResponse) is None


def test_redis_failure_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reference_cache, "redis_client", BrokenRedis())
    key = reference_cache.make_reference_cache_key("skills", {"q": None})

    assert reference_cache.get_cached(key, SkillListResponse) is None
    # set도 예외를 삼켜야 한다(Redis 장애가 API를 막지 않음)
    reference_cache.set_cached(key, SkillListResponse(skills=[]), ttl_seconds=10)


def test_reference_ttls_match_spec() -> None:
    # 체크리스트 #3/#4: 참조 데이터 24h, company-by-skill 6h
    assert settings.reference_cache_ttl_seconds == 24 * 60 * 60
    assert settings.company_by_skill_cache_ttl_seconds == 6 * 60 * 60


# ---------------------------------------------------------------------------
# 2. 엔드포인트 캐싱 — 히트 시 DB 미조회 + 엔드포인트별 TTL
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fake = FakeRedis()
    monkeypatch.setattr(reference_cache, "redis_client", fake)

    def override_get_session() -> Iterator[None]:
        yield None  # CRUD를 패치하므로 실제 세션은 쓰지 않는다

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.fake_redis = fake  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("router_module", "crud_name", "path", "params", "empty_body", "expected_ttl"),
    [
        ("app.routers.skills", "search_skills", "/skills", {"q": "py"}, {"skills": []}, 24 * 60 * 60),
        (
            "app.routers.job_categories",
            "list_job_categories",
            "/api/v1/job-categories",
            {},
            {"categories": []},
            24 * 60 * 60,
        ),
        ("app.routers.cert", "search_certs", "/api/v1/certs", {"q": "aws"}, {"certs": []}, 24 * 60 * 60),
        (
            "app.routers.company",
            "find_skill_id",
            "/api/v1/company/by-skill",
            {"skill": "Kotlin"},
            None,
            6 * 60 * 60,
        ),
    ],
)
def test_endpoint_caches_and_hit_skips_db(
    monkeypatch: pytest.MonkeyPatch,
    client_with_fake_redis: TestClient,
    router_module: str,
    crud_name: str,
    path: str,
    params: dict,
    empty_body,
    expected_ttl: int,
) -> None:
    import importlib

    module = importlib.import_module(router_module)
    calls = {"n": 0}
    # find_skill_id는 None을 반환하면 빈 결과 분기(추가 DB 조회 없음), 나머지는 빈 리스트.
    return_value = None if crud_name == "find_skill_id" else []

    def fake_crud(*args, **kwargs):
        calls["n"] += 1
        return return_value

    monkeypatch.setattr(module, crud_name, fake_crud)

    # 1) 미스 — DB(crud) 1회 호출 후 응답 캐싱
    first = client_with_fake_redis.get(path, params=params)
    assert first.status_code == 200
    if empty_body is not None:
        assert first.json() == empty_body
    assert calls["n"] == 1

    fake = client_with_fake_redis.fake_redis  # type: ignore[attr-defined]
    assert len(fake.setex_calls) == 1
    assert fake.setex_calls[0][1] == expected_ttl  # 엔드포인트별 TTL 확인(#3/#4)

    # 2) 히트 — 동일 응답, DB 재조회 없음(#7: 두 번째 요청은 캐시 히트)
    second = client_with_fake_redis.get(path, params=params)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert calls["n"] == 1  # crud가 다시 불리지 않았다 = 캐시 히트


def test_repeat_request_served_from_real_redis_with_expected_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#7: 실제 Redis 명령 구현체(fakeredis)에 대해 미스→히트를 검증한다.

    FakeRedis 스텁이 아니라 실제 setex/get/ttl/keys를 구현한 fakeredis를 써서
    (1) 캐시 키가 실제로 생기고 (2) TTL이 24h 밴드에 들며 (3) 두 번째 동일
    요청이 DB를 다시 타지 않는 것을 확인한다.
    """
    from collections.abc import Iterator

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.routers.skills as skills_router
    from app.core.db import Base
    from app.models import Skill, SkillAlias

    fakeredis = pytest.importorskip("fakeredis")
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(reference_cache, "redis_client", fake)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as seed:
        py = Skill(canonical="Python", category="language", is_ambiguous=False)
        seed.add(py)
        seed.flush()
        seed.add(SkillAlias(skill_id=py.id, alias="python", is_korean=False))
        seed.commit()

    db_calls = {"n": 0}
    original = skills_router.search_skills

    def counting_search(*args, **kwargs):
        db_calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(skills_router, "search_skills", counting_search)

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        first = client.get("/skills", params={"q": "py", "limit": 20})
        second = client.get("/skills", params={"q": "py", "limit": 20})
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["skills"][0]["canonical"] == "Python"
    assert second.json() == first.json()  # 히트 응답 스키마 동일
    assert db_calls["n"] == 1  # 두 번째 요청은 DB를 다시 타지 않았다

    keys = fake.keys("refcache:v1:skills:*")
    assert len(keys) == 1  # 실제 Redis에 캐시 키가 생성됨
    ttl = fake.ttl(keys[0])
    assert 24 * 60 * 60 - 10 <= ttl <= 24 * 60 * 60  # 24h TTL 밴드
