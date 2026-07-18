from app.core.config import settings
from app.services.rag import embedder


def test_warmup_is_noop_when_vector_search_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_vector_search", False)

    def _load_should_not_be_called():
        raise AssertionError("enable_vector_search가 꺼져 있으면 _load가 호출되면 안 된다")

    monkeypatch.setattr(embedder, "_load", _load_should_not_be_called)

    assert embedder.warmup() is False


def test_warmup_delegates_to_load_and_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(settings, "enable_vector_search", True)
    monkeypatch.setattr(embedder, "_load", lambda: object())

    assert embedder.warmup() is True


def test_warmup_returns_false_when_load_fails(monkeypatch):
    monkeypatch.setattr(settings, "enable_vector_search", True)
    monkeypatch.setattr(embedder, "_load", lambda: None)

    assert embedder.warmup() is False
