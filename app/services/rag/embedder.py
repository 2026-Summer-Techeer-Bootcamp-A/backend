"""쿼리 임베더 — BGE-M3 CPU 추론(fastembed/onnx).

저장된 공고 임베딩이 BGE-M3(normalize=True)라, 쿼리도 같은 모델로 임베딩해야 같은
공간에서 코사인 검색이 된다. 프로덕션 VM에 GPU가 없으므로 fastembed의 onnx 백엔드로
CPU 추론한다. 모델 로딩은 RAM 2~3GB를 쓰므로 settings.enable_vector_search가 켜졌을
때만 지연 로딩한다. 라이브러리나 모델이 없으면 None을 반환해 vector_tool이 폴백한다.
"""

from __future__ import annotations

import math

from app.core.config import settings

_model = None
_load_failed = False


def _load():
    """fastembed BGE-M3 모델을 최초 1회 지연 로딩. 실패 시 None(폴백)."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=settings.embedding_model)
    except Exception:
        _load_failed = True
        _model = None
    return _model


def embed_query(query: str) -> list[float] | None:
    """쿼리 문자열을 1024차원 L2 정규화 벡터로. 비활성/실패 시 None."""
    if not settings.enable_vector_search or not query.strip():
        return None
    model = _load()
    if model is None:
        return None
    try:
        vec = next(iter(model.embed([query])))
    except Exception:
        return None
    values = [float(x) for x in vec]
    if len(values) != settings.embedding_dim:
        return None
    norm = math.sqrt(sum(x * x for x in values)) or 1.0
    return [x / norm for x in values]
