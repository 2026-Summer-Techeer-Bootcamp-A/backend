import json

from fastapi.testclient import TestClient

from app.main import app


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value


HN_ITEMS = [
    {
        "title": "Show HN: Test",
        "url": "https://example.com/a",
        "comments_url": "https://news.ycombinator.com/item?id=1",
        "points": 120,
        "comments_count": 45,
    }
]


def _patch(monkeypatch, fake_redis, fetcher):
    from app.services import news as news_service

    monkeypatch.setattr(news_service, "redis_client", fake_redis)
    monkeypatch.setitem(news_service._FETCHERS, "hackernews", fetcher)
    return news_service


def test_cache_miss_fetches_and_caches(monkeypatch):
    fake = FakeRedis()
    _patch(monkeypatch, fake, lambda limit: HN_ITEMS)
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews", "limit": 5})

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "hackernews"
    assert body["error"] is False and body["stale"] is False
    assert body["items"][0]["title"] == "Show HN: Test"
    assert "news:hackernews" in fake.store
    assert "news:hackernews:stale" in fake.store


def test_cache_hit_does_not_fetch(monkeypatch):
    fake = FakeRedis()
    payload = {
        "source": "hackernews",
        "items": HN_ITEMS,
        "fetched_at": "2026-07-13T00:00:00+00:00",
        "stale": False,
        "error": False,
    }
    fake.store["news:hackernews"] = json.dumps(payload, ensure_ascii=False)

    def boom(limit):
        raise AssertionError("must not fetch on cache hit")

    _patch(monkeypatch, fake, boom)
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews"})

    assert res.status_code == 200
    assert res.json()["fetched_at"] == "2026-07-13T00:00:00+00:00"


def test_fetch_failure_falls_back_to_stale(monkeypatch):
    fake = FakeRedis()
    payload = {
        "source": "hackernews",
        "items": HN_ITEMS,
        "fetched_at": "2026-07-12T00:00:00+00:00",
        "stale": False,
        "error": False,
    }
    fake.store["news:hackernews:stale"] = json.dumps(payload, ensure_ascii=False)

    def boom(limit):
        raise RuntimeError("network down")

    _patch(monkeypatch, fake, boom)
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews"})

    assert res.status_code == 200
    body = res.json()
    assert body["stale"] is True and body["error"] is False
    assert body["items"][0]["title"] == "Show HN: Test"


def test_fetch_failure_without_stale_returns_error_flag(monkeypatch):
    fake = FakeRedis()

    def boom(limit):
        raise RuntimeError("network down")

    _patch(monkeypatch, fake, boom)
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews"})

    assert res.status_code == 200
    body = res.json()
    assert body["error"] is True and body["items"] == []


def test_limit_slices_cached_items(monkeypatch):
    fake = FakeRedis()
    many = [dict(HN_ITEMS[0], title=f"t{i}") for i in range(10)]
    fake.store["news:hackernews"] = json.dumps(
        {
            "source": "hackernews",
            "items": many,
            "fetched_at": "2026-07-13T00:00:00+00:00",
            "stale": False,
            "error": False,
        },
        ensure_ascii=False,
    )
    _patch(monkeypatch, fake, lambda limit: many)
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews", "limit": 3})

    assert len(res.json()["items"]) == 3


def test_invalid_source_rejected():
    client = TestClient(app)
    res = client.get("/api/v1/news", params={"source": "reddit"})
    assert res.status_code == 422
