import json

import pytest
from redis.exceptions import RedisError

from app.schemas.search import SearchCachePayload
from app.services import search_cache


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


@pytest.fixture
def payload() -> SearchCachePayload:
    return SearchCachePayload(
        postings=[{"id": 1, "title": "Python Developer", "company": "Kakao", "pool": "domestic"}],
        skills=[{"canonical": "Python", "category": "language"}],
        companies=[{"company": "Kakao", "posting_count": 1}],
    )


def test_cache_key_normalizes_case_and_whitespace() -> None:
    assert search_cache.make_search_cache_key(" Python ", 5) == search_cache.make_search_cache_key("python", 5)
    assert search_cache.make_search_cache_key("python", 5) != search_cache.make_search_cache_key("python", 10)


def test_cache_round_trip_uses_three_hour_ttl(monkeypatch: pytest.MonkeyPatch, payload: SearchCachePayload) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(search_cache, "redis_client", fake)

    search_cache.set_cached_search("Python", 5, payload)
    cached = search_cache.get_cached_search("python", 5)

    assert cached == payload
    assert fake.setex_calls[0][1] == 10_800


def test_invalid_cached_payload_is_treated_as_cache_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    key = search_cache.make_search_cache_key("Python", 5)
    fake.store[key] = json.dumps({"wrong": "shape"})
    monkeypatch.setattr(search_cache, "redis_client", fake)

    assert search_cache.get_cached_search("Python", 5) is None


def test_redis_failure_is_treated_as_cache_miss(monkeypatch: pytest.MonkeyPatch, payload: SearchCachePayload) -> None:
    monkeypatch.setattr(search_cache, "redis_client", BrokenRedis())

    assert search_cache.get_cached_search("Python", 5) is None
    search_cache.set_cached_search("Python", 5, payload)
