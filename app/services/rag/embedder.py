"""쿼리 임베더 — BGE-M3 CPU 추론(sentence-transformers).

저장된 공고 임베딩이 BGE-M3(normalize=True)라, 쿼리도 같은 모델로 임베딩해야 같은
공간에서 코사인 검색이 된다. posting_embedding 테이블의 벡터를 만든 라이브러리와
동일하게 sentence-transformers를 써서 같은 임베딩 공간을 보장한다. 프로덕션 VM에
GPU가 없으므로 CPU로 추론한다. 모델 로딩은 RAM 2~3GB를 쓰므로 settings.enable_vector_search가
켜졌을 때만 지연 로딩한다. 라이브러리나 모델이 없으면 None을 반환해 vector_tool이 폴백한다.
"""

from __future__ import annotations

import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

_model = None
_load_failed = False


def _load():
    """sentence-transformers BGE-M3 모델을 최초 1회 지연 로딩. 실패 시 None(폴백)."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        try:
            _model = SentenceTransformer(settings.embedding_model, device="cpu")
        except (PermissionError, OSError):
            cache_dir = "/tmp/models"
            os.makedirs(cache_dir, exist_ok=True)
            _model = SentenceTransformer(settings.embedding_model, device="cpu", cache_folder=cache_dir)
    except Exception:
        # 폴백(벡터 검색 비활성화 후 sql/graph로 대체) 자체는 의도된 동작이므로 유지한다.
        # 다만 이 예외를 통째로 삼키면(예: /models 볼륨 권한 문제로 모델 다운로드가 매번
        # 실패) 벡터 검색이 프로덕션에서 단 한 번도 성공하지 못해도 아무 흔적이 안 남는다.
        logger.warning("BGE-M3 임베딩 모델 로딩 실패, 벡터 검색을 비활성화하고 폴백합니다", exc_info=True)
        _load_failed = True
        _model = None
    return _model


def warmup() -> bool:
    """벡터 검색이 켜져 있으면 BGE-M3 모델을 미리 로드한다.

    첫 벡터 질의 때 지연 로딩되는 모델을 시작 시점에 백그라운드로 앞당겨 로드해,
    첫 사용자 질의가 모델 로딩(CPU 수십 초)을 통째로 떠안지 않게 한다. 벡터 검색이
    꺼져 있으면 즉시 no-op이다. 로드 성공 여부를 반환한다.
    """
    if not settings.enable_vector_search:
        return False
    return _load() is not None


def embed_query(query: str) -> list[float] | None:
    """쿼리 문자열을 1024차원 L2 정규화 벡터로. 비활성/실패 시 None."""
    if not settings.enable_vector_search or not query.strip():
        return None
    model = _load()
    if model is None:
        return None
    try:
        vec = model.encode([query], normalize_embeddings=True)[0]
    except Exception:
        logger.warning("쿼리 임베딩 인코딩 실패, 벡터 검색을 건너뛰고 폴백합니다", exc_info=True)
        return None
    values = [float(x) for x in vec]
    if len(values) != settings.embedding_dim:
        return None
    return values
