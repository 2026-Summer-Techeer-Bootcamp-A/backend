import json
import logging
import secrets
from typing import Any

import redis
from app.core.config import settings

logger = logging.getLogger(__name__)

# FastAPI의 엔드포인트 내에서 단순한 I/O 블로킹 작업으로 처리하기 위해 동기식 Redis 클라이언트 사용.
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=0.5,
    socket_timeout=0.5,
)
RESUME_CONFIRM_SESSION_KEY_PREFIX = "resume_confirm:"


def add_token_to_blocklist(token: str, expires_in_seconds: int) -> None:
    # 로그아웃된 토큰이 다시 사용되지 못하도록 Redis에 저장.
    # 토큰 자체가 키가 되며, 원래 토큰의 남은 만료 시간만큼만 저장하여 메모리 누수 방지.
    try:
        redis_client.setex(f"blocklist:{token}", expires_in_seconds, "1")
    except redis.RedisError:
        logger.warning("Failed to add token to blocklist in Redis", exc_info=True)


def is_token_blocklisted(token: str) -> bool:
    try:
        return redis_client.exists(f"blocklist:{token}") > 0
    except redis.RedisError:
        logger.warning("Failed to check if token is blocklisted in Redis", exc_info=True)
        return False


def create_resume_confirm_session(payload: dict[str, Any], ttl_seconds: int) -> str:
    for _ in range(5):
        session_id = secrets.token_hex(16)
        redis_key = f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}"
        try:
            if redis_client.exists(redis_key) > 0:
                continue
            redis_client.setex(
                redis_key,
                ttl_seconds,
                json.dumps(payload, ensure_ascii=False),
            )
            return session_id
        except redis.RedisError:
            logger.warning("Failed to access Redis during create_resume_confirm_session", exc_info=True)
            return session_id

    raise RuntimeError("could not allocate unique resume session id")


def get_resume_confirm_session(session_id: str) -> dict[str, Any] | None:
    try:
        value = redis_client.get(f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}")
    except redis.RedisError:
        logger.warning("Failed to get resume confirm session from Redis", exc_info=True)
        return None

    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        logger.warning("Failed to deserialize resume confirm session payload", exc_info=True)
        return None


def resume_confirm_session_exists(session_id: str) -> bool:
    try:
        return redis_client.exists(f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}") > 0
    except redis.RedisError:
        logger.warning("Failed to check resume confirm session existence in Redis", exc_info=True)
        return False

