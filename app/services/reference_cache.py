"""참조성 데이터용 Redis 캐시.

스킬·직무 카테고리·자격증·기술별 기업 목록은 수집기가 돌 때만 바뀌는 참조
데이터라 캐시 효율이 높다. 이 엔드포인트 자체가 빨라지는 효과도 있지만,
DB 왕복을 줄여 무거운 쿼리들의 커넥션 경합 상대를 줄이는 효과가 더 크다.

search_cache와 동일하게 성능 보조 기능이므로, Redis 장애가 API를 오래 막지
않도록 인증·세션용 공용 클라이언트와 분리하고 짧은 타임아웃을 적용하며 모든
Redis 오류는 캐시 미스로 취급(fail-open)한다.
"""

import hashlib
import json
import logging
from typing import TypeVar

import redis
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

REFERENCE_CACHE_KEY_PREFIX = "refcache:v1"

redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=settings.reference_cache_socket_timeout_seconds,
    socket_timeout=settings.reference_cache_socket_timeout_seconds,
)

T = TypeVar("T", bound=BaseModel)


def make_reference_cache_key(namespace: str, params: dict[str, object]) -> str:
    """엔드포인트(namespace)별 + 파라미터별로 구분되는 고정 길이 키.

    파라미터를 정렬된 JSON으로 직렬화해 해시하므로 값이 하나라도 다르면 키가
    달라진다(같은 엔드포인트라도 파라미터가 다르면 다른 캐시로 취급). q 같은
    자유 입력이 Redis 키에 그대로 노출되지 않도록 해시로 감싼다.
    """
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{REFERENCE_CACHE_KEY_PREFIX}:{namespace}:{digest}"


def get_cached(key: str, model: type[T]) -> T | None:
    """캐시된 응답을 주어진 pydantic 모델로 검증해 돌려준다.

    미스·Redis 오류·스키마 불일치는 모두 None(캐시 미스)으로 취급한다 —
    깨진 캐시 데이터가 잘못된 스키마로 나가지 않도록 검증 실패도 미스 처리.
    """
    try:
        cached = redis_client.get(key)
    except RedisError:
        logger.warning("참조 캐시 조회 실패: key=%s", key, exc_info=True)
        return None

    if cached is None:
        return None

    try:
        return model.model_validate_json(cached)
    except (ValidationError, ValueError, TypeError):
        logger.warning("참조 캐시 데이터 검증 실패: key=%s", key, exc_info=True)
        return None


def set_cached(key: str, payload: BaseModel, ttl_seconds: int) -> None:
    """응답을 TTL과 함께 캐시에 적재한다. Redis 오류는 조용히 무시(fail-open)."""
    try:
        redis_client.setex(key, ttl_seconds, payload.model_dump_json())
    except RedisError:
        logger.warning("참조 캐시 저장 실패: key=%s", key, exc_info=True)
