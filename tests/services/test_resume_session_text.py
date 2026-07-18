from app.core import redis as redis_mod
from app.services.resume import parse_resume_text


def test_session_roundtrip_preserves_resume_text(monkeypatch):
    store: dict[str, str] = {}

    class _FakeRedis:
        def exists(self, k):
            return 1 if k in store else 0

        def setex(self, k, ttl, v):
            store[k] = v

        def get(self, k):
            return store.get(k)

    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedis())

    sid = redis_mod.create_resume_confirm_session(
        {"skills": [], "certs": [], "position": None, "resume_text": "백엔드 4년차. FastAPI 정산 API 운영."},
        ttl_seconds=3600,
    )
    assert redis_mod.get_resume_text_from_session(sid) == "백엔드 4년차. FastAPI 정산 API 운영."
    assert redis_mod.get_resume_text_from_session("nope") is None


def test_parse_resume_text_preserves_raw_text():
    text = "백엔드 4년차. FastAPI 정산 API 운영. PostgreSQL, Redis 사용."
    result = parse_resume_text(text, taxonomy=[], cert_names=[])
    assert result.resume_text == text
