"""POST /chat — 하이브리드 Agentic + Graph RAG 엔드포인트(v2 구조화 JSON).

설계: docs/superpowers/specs/2026-07-10-rag-hybrid-agentic-graph-design.md 5절.
"""

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.deps import SessionDep
from app.core.redis import get_resume_text_from_session
from app.routers.match import get_user_from_optional_authorization
from app.services.match import get_skill_ids_from_resume
from app.services.rag.pipeline import run_chat, run_chat_events
from app.services.rag.schemas import ChatRequest, ChatResponse

router = APIRouter()


def _resolve_owned_skill_ids(
    session: SessionDep, resume_id: int | None, authorization: str | None
) -> set[int] | None:
    """resume_id가 없으면 이력서 미첨부(None) — resume_gap/resume_coverage가 아닌 일반
    질문은 이 값을 아예 쓰지 않으므로 기존 동작에 영향이 없다."""
    if resume_id is None:
        return None
    current_user = get_user_from_optional_authorization(session, authorization)
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    return get_skill_ids_from_resume(session=session, resume_id=resume_id, current_user=current_user)


def _resolve_resume_text(resume_session_id: str | None) -> str | None:
    """resume_session_id가 없거나 세션이 만료됐으면 None — pipeline._dispatch가 이를
    보고 기존 태그 기반 비교로 우아하게 강등한다(조용한 실패 없이)."""
    if resume_session_id is None:
        return None
    return get_resume_text_from_session(resume_session_id)


@router.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> ChatResponse:
    owned_skill_ids = _resolve_owned_skill_ids(session, body.resume_id, authorization)
    resume_text = _resolve_resume_text(body.resume_session_id)
    return run_chat(
        session,
        body.question,
        body.pool,
        verbose=body.verbose,
        owned_skill_ids=owned_skill_ids,
        posting_ids=body.posting_ids,
        resume_text=resume_text,
    )


@router.post("/chat/stream")
def chat_stream(
    body: ChatRequest,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    owned_skill_ids = _resolve_owned_skill_ids(session, body.resume_id, authorization)
    resume_text = _resolve_resume_text(body.resume_session_id)

    def gen() -> Iterator[str]:
        for event in run_chat_events(
            session,
            body.question,
            body.pool,
            verbose=body.verbose,
            owned_skill_ids=owned_skill_ids,
            posting_ids=body.posting_ids,
            resume_text=resume_text,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
