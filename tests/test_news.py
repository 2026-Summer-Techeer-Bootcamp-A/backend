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


GEEKNEWS_HTML_FIXTURE = (
    "<html><body><div class=\"topics\">"
    "<div class='topic_row' data-topic-state-id='31384' data-topic-voteable='1'>"
    "<div class=votenum>1</div>"
    "<div class=topictitle><span id='dead31384'></span>"
    "<a href='https://www.youtube.com/watch?v=abc' rel='nofollow' id='tr1'>"
    "<h2 class='topic-title-heading'>고객 이탈을 막는법 &amp; 노하우</h2></a>"
    " <span class=topicurl>(youtube.com)</span></div>"
    "<div class='topicinfo'><span id='tp31384'>19</span> points by <a href='/@neo'>GN</a>"
    " | <a href='topic?id=31384&go=comments' class=u data-topic-comment-topic-id='31384'"
    " data-topic-comment-count='3'>댓글과 토론</a></div></div>"
    "<div class='topic_row' data-topic-state-id='31313' data-topic-voteable='1'>"
    "<div class=votenum>2</div>"
    "<div class=topictitle><span id='dead31313'></span>"
    "<a href='topic?id=31313' id='tr2'>"
    "<h2 class='topic-title-heading'>Ask GN: 사이드 프로젝트</h2></a></div>"
    "<div class='topicinfo'><span id='tp31313'>75</span> points by <a href='/@x'>x</a>"
    " | <a href='topic?id=31313&go=comments' class=u data-topic-comment-topic-id='31313'"
    " data-topic-comment-count='28'>댓글과 토론</a></div></div>"
    "</div></body></html>"
)


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("utf-8")

    def raise_for_status(self):
        return None


def test_fetch_geeknews_parses_frontpage_html_with_points_and_comments(monkeypatch):
    from app.services import news as news_service

    def fake_get(url, timeout):
        if url.rstrip("/") == "https://news.hada.io":
            return FakeResponse(GEEKNEWS_HTML_FIXTURE.encode("utf-8"))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(news_service.requests, "get", fake_get)

    items = news_service._fetch_geeknews(10)

    assert len(items) == 2
    first = items[0]
    assert first["title"] == "고객 이탈을 막는법 & 노하우"
    assert first["url"] == "https://www.youtube.com/watch?v=abc"
    assert first["comments_url"] == "https://news.hada.io/topic?id=31384"
    assert first["points"] == 19
    assert first["comments_count"] == 3

    second = items[1]
    assert second["title"] == "Ask GN: 사이드 프로젝트"
    # relative topic link must be converted to an absolute URL
    assert second["url"] == "https://news.hada.io/topic?id=31313"
    assert second["comments_url"] == "https://news.hada.io/topic?id=31313"
    assert second["points"] == 75
    assert second["comments_count"] == 28


def test_fetch_geeknews_falls_back_to_atom_when_html_unusable(monkeypatch):
    from app.services import news as news_service

    def fake_get(url, timeout):
        if url.rstrip("/") == "https://news.hada.io":
            return FakeResponse(b"<html><body>no topic rows here</body></html>")
        if url == "https://news.hada.io/rss/news":
            return FakeResponse(ATOM_FIXTURE.encode("utf-8"))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(news_service.requests, "get", fake_get)

    items = news_service._fetch_geeknews(10)

    assert len(items) == 2
    assert items[0]["title"] == "LARP - 진지한 창업자를 위한 매출 인프라"
    assert items[0]["url"] == "https://news.hada.io/topic?id=31396"
    assert items[0]["comments_url"] == "https://news.hada.io/topic?id=31396"
    assert items[1]["title"] == "Tiny Emulators"
    assert items[1]["url"] == "https://news.hada.io/topic?id=31395"


def test_fetch_geeknews_falls_back_to_atom_when_html_request_raises(monkeypatch):
    from app.services import news as news_service

    def fake_get(url, timeout):
        if url.rstrip("/") == "https://news.hada.io":
            raise RuntimeError("frontpage down")
        if url == "https://news.hada.io/rss/news":
            return FakeResponse(ATOM_FIXTURE.encode("utf-8"))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(news_service.requests, "get", fake_get)

    items = news_service._fetch_geeknews(10)

    assert len(items) == 2
    assert items[0]["title"] == "LARP - 진지한 창업자를 위한 매출 인프라"


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
