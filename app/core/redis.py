import redis
from app.core.config import settings

# FastAPI의 엔드포인트 내에서 단순한 I/O 블로킹 작업으로 처리하기 위해 동기식 Redis 클라이언트 사용.
redis_client = redis.from_url(settings.redis_url, decode_responses=True)

def add_token_to_blocklist(token: str, expires_in_seconds: int):
    # 로그아웃된 토큰이 다시 사용되지 못하도록 Redis에 저장.
    # 토큰 자체가 키가 되며, 원래 토큰의 남은 만료 시간만큼만 저장하여 메모리 누수 방지.
    redis_client.setex(f"blocklist:{token}", expires_in_seconds, "1")

def is_token_blocklisted(token: str) -> bool:
    # 들어온 토큰이 로그아웃 처리되어 블록리스트에 있는지 O(1) 시간 복잡도로 검사.
    return redis_client.exists(f"blocklist:{token}") > 0
