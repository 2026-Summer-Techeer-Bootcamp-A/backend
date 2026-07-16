"""POST /chat — 하이브리드 Agentic + Graph RAG 엔드포인트(v2 구조화 JSON).

설계: docs/superpowers/specs/2026-07-10-rag-hybrid-agentic-graph-design.md 5절.
"""

import json
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.deps import SessionDep
from app.services.rag.pipeline import run_chat, run_chat_events
from app.services.rag.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, session: SessionDep) -> ChatResponse:
    return run_chat(session, body.question, body.pool, verbose=body.verbose)


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, session: SessionDep) -> StreamingResponse:
    def gen() -> Iterator[str]:
        for event in run_chat_events(session, body.question, body.pool, verbose=body.verbose):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
