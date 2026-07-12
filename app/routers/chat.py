"""POST /chat — 하이브리드 Agentic + Graph RAG 엔드포인트(v2 구조화 JSON).

설계: docs/superpowers/specs/2026-07-10-rag-hybrid-agentic-graph-design.md 5절.
"""

from fastapi import APIRouter

from app.core.deps import SessionDep
from app.services.rag.pipeline import run_chat
from app.services.rag.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, session: SessionDep) -> ChatResponse:
    return run_chat(session, body.question, body.pool)
