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


ATOM_FIXTURE = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
<title>GeekNews</title>
<link rel='alternate' type='text/html' href='https://news.hada.io' />
<link rel='self' type='application/atom+xml' href='https://news.hada.io/rss/news' />
<id>https://news.hada.io/rss/news</id>
<updated>2026-07-13T19:34:11+09:00</updated>
<entry>
  <title><![CDATA[LARP - 진지한 창업자를 위한 매출 인프라]]></title>
  <link rel='alternate' type='text/html' href='https://news.hada.io/topic?id=31396' />
  <id>https://news.hada.io/topic?id=31396</id>
  <updated>2026-07-13T19:34:11+09:00</updated>
  <published>2026-07-13T19:34:11+09:00</published>
  <content type='html'><![CDATA[<p>body</p>]]></content>
</entry>
<entry>
  <title><![CDATA[Tiny Emulators]]></title>
  <link rel='alternate' type='text/html' href='https://news.hada.io/topic?id=31395' />
  <id>https://news.hada.io/topic?id=31395</id>
  <updated>2026-07-13T17:37:21+09:00</updated>
</entry>
</feed>
"""


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


def test_fetch_geeknews_parses_atom_entries(monkeypatch):
    from app.services import news as news_service

    def fake_get(url, timeout):
        return FakeResponse(ATOM_FIXTURE.encode("utf-8"))

    monkeypatch.setattr(news_service.requests, "get", fake_get)

    items = news_service._fetch_geeknews(10)

    assert len(items) == 2
    assert items[0]["title"] == "LARP - 진지한 창업자를 위한 매출 인프라"
    assert items[0]["url"] == "https://news.hada.io/topic?id=31396"
    assert items[0]["comments_url"] == "https://news.hada.io/topic?id=31396"
    assert items[1]["title"] == "Tiny Emulators"
    assert items[1]["url"] == "https://news.hada.io/topic?id=31395"


def test_empty_fetch_result_not_cached_falls_back_to_stale(monkeypatch):
    fake = FakeRedis()
    payload = {
        "source": "hackernews",
        "items": HN_ITEMS,
        "fetched_at": "2026-07-12T00:00:00+00:00",
        "stale": False,
        "error": False,
    }
    fake.store["news:hackernews:stale"] = json.dumps(payload, ensure_ascii=False)

    _patch(monkeypatch, fake, lambda limit: [])
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews"})

    assert res.status_code == 200
    body = res.json()
    assert body["stale"] is True and body["error"] is False
    assert body["items"][0]["title"] == "Show HN: Test"
    # empty fetch result must not have been cached as a fresh payload
    assert "news:hackernews" not in fake.store


def test_empty_fetch_result_without_stale_returns_error_flag(monkeypatch):
    fake = FakeRedis()
    _patch(monkeypatch, fake, lambda limit: [])
    client = TestClient(app)

    res = client.get("/api/v1/news", params={"source": "hackernews"})

    assert res.status_code == 200
    body = res.json()
    assert body["error"] is True and body["items"] == []
    assert "news:hackernews" not in fake.store
