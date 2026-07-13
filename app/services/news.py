import html as html_lib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from app.core.redis import redis_client

NEWS_CACHE_TTL_SECONDS = 4 * 60 * 60
NEWS_STALE_TTL_SECONDS = 24 * 60 * 60
NEWS_FETCH_LIMIT = 30
_FETCH_TIMEOUT_SECONDS = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_hackernews(limit: int) -> list[dict]:
    response = requests.get(
        "https://hn.algolia.com/api/v1/search",
        params={"tags": "front_page", "hitsPerPage": limit},
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    items: list[dict] = []
    for hit in response.json().get("hits", [])[:limit]:
        thread_url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        items.append(
            {
                "title": hit.get("title") or "(제목 없음)",
                "url": hit.get("url") or thread_url,
                "comments_url": thread_url,
                "points": hit.get("points"),
                "comments_count": hit.get("num_comments"),
            }
        )
    return items


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _atom_entry_url(entry: ET.Element) -> str | None:
    """entry의 link 중 rel='alternate'를 우선하고, 없으면 첫 link를 사용."""
    links = entry.findall(f"{_ATOM_NS}link")
    for link in links:
        if link.get("rel") == "alternate" and link.get("href"):
            return link.get("href")
    for link in links:
        if link.get("href"):
            return link.get("href")
    return None


def _parse_geeknews_atom(root: ET.Element, limit: int) -> list[dict]:
    items: list[dict] = []
    for entry in root.iter(f"{_ATOM_NS}entry"):
        title = entry.findtext(f"{_ATOM_NS}title")
        url = _atom_entry_url(entry)
        if not title or not url:
            continue
        items.append({"title": title, "url": url, "comments_url": url})
        if len(items) >= limit:
            break
    return items


def _parse_geeknews_rss(root: ET.Element, limit: int) -> list[dict]:
    """news.hada.io가 RSS 2.0으로 되돌아갈 경우를 대비한 폴백 파서."""
    items: list[dict] = []
    for item in root.iter("item"):
        title = item.findtext("title")
        link = item.findtext("link")
        if not title or not link:
            continue
        items.append({"title": title, "url": link, "comments_url": link})
        if len(items) >= limit:
            break
    return items


_GEEKNEWS_BASE = "https://news.hada.io"

# 목록 페이지의 topic_row 블록 경계. 속성 인용부호가 바뀌어도 견디도록 둘 다 허용.
_GEEKNEWS_ROW_SPLIT_RE = re.compile(r"<div class=['\"]topic_row['\"]")
_GEEKNEWS_TOPIC_ID_RE = re.compile(r"data-topic-state-id=['\"](\d+)['\"]")
_GEEKNEWS_TITLE_RE = re.compile(
    r"<div class=['\"]?topictitle['\"]?>.*?<a href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
    re.S,
)
_GEEKNEWS_COMMENT_COUNT_RE = re.compile(r"data-topic-comment-count=['\"](\d+)['\"]")
_TAG_RE = re.compile(r"<[^>]+>")


def _geeknews_absolute_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{_GEEKNEWS_BASE}/{href.lstrip('/')}"


def _parse_geeknews_html(page: str, limit: int) -> list[dict]:
    """목록 페이지 HTML에서 topic_row 단위로 제목/원문 URL/포인트/댓글 수를 뽑는다.

    포인트는 votenum이 아니라 topicinfo의 <span id='tp{id}'>N</span>에 있다.
    (votenum은 페이지 내 표시 순번이라 실제 포인트와 다르다.)
    """
    items: list[dict] = []
    for chunk in _GEEKNEWS_ROW_SPLIT_RE.split(page)[1:]:
        id_match = _GEEKNEWS_TOPIC_ID_RE.search(chunk)
        title_match = _GEEKNEWS_TITLE_RE.search(chunk)
        if not id_match or not title_match:
            continue
        topic_id = id_match.group(1)
        title = html_lib.unescape(_TAG_RE.sub("", title_match.group(2))).strip()
        if not title:
            continue
        points_match = re.search(rf"<span id=['\"]tp{topic_id}['\"]>(\d+)</span>", chunk)
        comments_match = _GEEKNEWS_COMMENT_COUNT_RE.search(chunk)
        items.append(
            {
                "title": title,
                "url": _geeknews_absolute_url(html_lib.unescape(title_match.group(1))),
                "comments_url": f"{_GEEKNEWS_BASE}/topic?id={topic_id}",
                "points": int(points_match.group(1)) if points_match else None,
                "comments_count": int(comments_match.group(1)) if comments_match else None,
            }
        )
        if len(items) >= limit:
            break
    return items


def _fetch_geeknews_frontpage(limit: int) -> list[dict]:
    response = requests.get(f"{_GEEKNEWS_BASE}/", timeout=_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return _parse_geeknews_html(response.text, limit)


def _fetch_geeknews_feed(limit: int) -> list[dict]:
    # news.hada.io/rss/news는 이름과 달리 RSS 2.0이 아니라 Atom 피드를 반환한다
    # (<feed>/<entry>, link는 rel='alternate' href 속성). 네임스페이스 인식 파싱을
    # 우선 시도하고, 포맷이 다시 RSS로 바뀌는 경우를 대비해 <item> 폴백도 유지한다.
    response = requests.get(f"{_GEEKNEWS_BASE}/rss/news", timeout=_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = _parse_geeknews_atom(root, limit)
    if not items:
        items = _parse_geeknews_rss(root, limit)
    return items


def _fetch_geeknews(limit: int) -> list[dict]:
    # 1차: 목록 페이지 HTML. Atom 피드에는 없는 포인트/댓글 수까지 얻을 수 있다.
    # 마크업이 바뀌어 파싱이 실패하거나 0건이면 Atom 피드로 폴백한다
    # (이 경우 title/url만 채워진다).
    try:
        items = _fetch_geeknews_frontpage(limit)
        if items:
            return items
    except Exception:
        pass
    return _fetch_geeknews_feed(limit)


def _fetch_github(limit: int) -> list[dict]:
    created_after = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    response = requests.get(
        "https://api.github.com/search/repositories",
        params={
            "q": f"created:>{created_after}",
            "sort": "stars",
            "order": "desc",
            "per_page": limit,
        },
        headers={"Accept": "application/vnd.github+json"},
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    items: list[dict] = []
    for repo in response.json().get("items", [])[:limit]:
        items.append(
            {
                "title": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description"),
                "language": repo.get("language"),
                "stars": repo.get("stargazers_count"),
            }
        )
    return items


_FETCHERS = {
    "hackernews": _fetch_hackernews,
    "geeknews": _fetch_geeknews,
    "github": _fetch_github,
}


def get_news(source: str, limit: int) -> dict:
    cache_key = f"news:{source}"
    cached = redis_client.get(cache_key)
    if cached is not None:
        payload = json.loads(cached)
        payload["items"] = payload["items"][:limit]
        return payload

    try:
        items = _FETCHERS[source](NEWS_FETCH_LIMIT)
        if not items:
            # 빈 결과는 파서/포맷 문제일 수 있는 사실상의 페치 실패로 취급한다.
            # 유효한 페이로드로 캐싱하면 오류가 TTL 동안 그대로 굳어버린다.
            raise ValueError(f"{source} fetch returned no items")
    except Exception:
        stale_raw = redis_client.get(f"{cache_key}:stale")
        if stale_raw is not None:
            payload = json.loads(stale_raw)
            payload["items"] = payload["items"][:limit]
            payload["stale"] = True
            return payload
        return {
            "source": source,
            "items": [],
            "fetched_at": _now_iso(),
            "stale": False,
            "error": True,
        }

    payload = {
        "source": source,
        "items": items,
        "fetched_at": _now_iso(),
        "stale": False,
        "error": False,
    }
    body = json.dumps(payload, ensure_ascii=False)
    redis_client.setex(cache_key, NEWS_CACHE_TTL_SECONDS, body)
    redis_client.setex(f"{cache_key}:stale", NEWS_STALE_TTL_SECONDS, body)
    payload["items"] = payload["items"][:limit]
    return payload
