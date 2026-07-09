import json
import secrets
from typing import Any

import redis
from app.core.config import settings

# FastAPI의 엔드포인트 내에서 단순한 I/O 블로킹 작업으로 처리하기 위해 동기식 Redis 클라이언트 사용.
redis_client = redis.from_url(settings.redis_url, decode_responses=True)
RESUME_CONFIRM_SESSION_KEY_PREFIX = "resume_confirm:"


def add_token_to_blocklist(token: str, expires_in_seconds: int):
    # 로그아웃된 토큰이 다시 사용되지 못하도록 Redis에 저장.
    # 토큰 자체가 키가 되며, 원래 토큰의 남은 만료 시간만큼만 저장하여 메모리 누수 방지.
    redis_client.setex(f"blocklist:{token}", expires_in_seconds, "1")


def is_token_blocklisted(token: str) -> bool:
    # 들어온 토큰이 로그아웃 처리되어 블록리스트에 있는지 O(1) 시간 복잡도로 검사.
    return redis_client.exists(f"blocklist:{token}") > 0


def create_resume_confirm_session(payload: dict[str, Any], ttl_seconds: int) -> str:
    for _ in range(5):
        session_id = secrets.token_hex(16)
        redis_key = f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}"
        if redis_client.exists(redis_key) > 0:
            continue
        redis_client.setex(
            redis_key,
            ttl_seconds,
            json.dumps(payload, ensure_ascii=False),
        )
        return session_id

    raise RuntimeError("could not allocate unique resume session id")


def get_resume_confirm_session(session_id: str) -> dict[str, Any] | None:
    value = redis_client.get(f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}")
    if value is None:
        return None
    return json.loads(value)


def resume_confirm_session_exists(session_id: str) -> bool:
    return redis_client.exists(f"{RESUME_CONFIRM_SESSION_KEY_PREFIX}{session_id}") > 0
