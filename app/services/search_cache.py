"""통합 검색 결과용 Redis 캐시."""

import hashlib
import logging

import redis
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.config import settings
from app.schemas.search import SearchCachePayload

logger = logging.getLogger(__name__)

SEARCH_CACHE_KEY_PREFIX = "search:v1"

# 검색 캐시는 성능 보조 기능이다. Redis 장애가 검색 API를 오래 막지 않도록
# 인증·이력서 세션용 공용 클라이언트와 분리하고 짧은 타임아웃을 적용한다.
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=settings.search_cache_socket_timeout_seconds,
    socket_timeout=settings.search_cache_socket_timeout_seconds,
)


def normalize_search_query(query: str) -> str:
    """DB 검색과 캐시 키가 동일한 공백 정리 규칙을 사용하게 한다."""
    return query.strip()


def make_search_cache_key(query: str, limit: int) -> str:
    """검색어 원문을 Redis 키에 노출하지 않는 고정 길이 키를 만든다."""
    normalized = normalize_search_query(query).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{SEARCH_CACHE_KEY_PREFIX}:{digest}:limit:{limit}"


def get_cached_search(query: str, limit: int) -> SearchCachePayload | None:
    key = make_search_cache_key(query, limit)

    try:
        cached = redis_client.get(key)
    except RedisError:
        logger.warning("검색 캐시 조회 실패", exc_info=True)
        return None

    if cached is None:
        return None

    try:
        return SearchCachePayload.model_validate_json(cached)
    except (ValidationError, ValueError, TypeError):
        logger.warning("검색 캐시 데이터 검증 실패: key=%s", key, exc_info=True)
        return None


def set_cached_search(query: str, limit: int, payload: SearchCachePayload) -> None:
    key = make_search_cache_key(query, limit)

    try:
        redis_client.setex(
            key,
            settings.search_cache_ttl_seconds,
            payload.model_dump_json(),
        )
    except RedisError:
        logger.warning("검색 캐시 저장 실패", exc_info=True)
