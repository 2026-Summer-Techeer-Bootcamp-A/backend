"""파이프라인 — router -> tools -> evaluator -> synthesis 오케스트레이션.

run_chat_events가 유일한 실행 경로다: SSE 계약과 정확히 같은 모양의 이벤트를 순서대로
yield하면서, 동시에 collect 딕셔너리에 조립용 원본 객체(Plan/Step/Citation/ToolResult 등)를
채운다. 스트리밍 라우트는 이벤트를 그대로 흘려보내고, 비스트리밍 run_chat은 이벤트를
소비만 하고 collect로 ChatResponse를 조립한다 — 두 경로가 로직을 공유해 드리프트가 없다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.services.rag.evaluator import evaluate
from app.services.rag.llm import get_llm
from app.services.rag.router import plan as make_plan
from app.services.rag.schemas import (
    ChatResponse,
    Citation,
    Confidence,
    Plan,
    Step,
    ToolResult,
)
from app.services.rag.synthesis import synthesize
from app.services.rag.tools import graph_tool, sql_tool, vector_tool


def _confidence_level(n: int) -> int:
    if n <= 0:
        return 0
    if n < 50:
        return 2
    if n < 500:
        return 3
    if n < 5000:
        return 4
    return 5


def _dispatch(session: Session, p: Plan) -> list[dict]:
    """intent에 따라 도구를 실행하고 tool_output 리스트를 반환. 실패 시 랭킹으로 폴백."""
    skill = p.entities.get("skill")
    pool = p.pool
    out: list[dict] = []

    if p.intent == "cooccurrence" and skill:
        r = graph_tool.co_occurring_skills(session, skill, pool)
        if r:
            out.append(r)
    elif p.intent == "semantic_search":
        r = vector_tool.semantic_search(session, p.subqueries[0] if p.subqueries else "", pool)
        if r:
            out.append(r)
    elif p.intent == "skill_demand" and skill:
        r = sql_tool.skill_demand(session, skill, pool)
        if r:
            out.append(r)
    elif p.intent == "concept_ranking":
        out.append(sql_tool.top_concepts(session, pool))
    elif p.intent == "cert_ranking":
        out.append(sql_tool.top_certs(session, pool))
    elif p.intent == "region_distribution":
        out.append(sql_tool.top_locations(session, pool))

    if not out:  # 위에서 못 채웠으면(대상 미해소 등) 기술 랭킹으로 폴백
        out.append(sql_tool.top_skills(session, pool))
    return out


def run_chat_events(
    session: Session,
    question: str,
    pool: str | None = None,
    *,
    collect: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """SSE 계약과 정확히 같은 모양의 이벤트를 순서대로 yield한다.

    collect가 주어지면(run_chat이 넘김) 스트리밍 페이로드에는 없는 조립용 원본 객체도
    함께 채운다 — 계약 밖 필드를 실제 SSE 프레임에는 절대 섞지 않기 위한 사이드채널이다.
    """
    if collect is None:
        collect = {}
    collect["steps"] = []

    try:
        llm = get_llm()
        p, plan_degraded = make_plan(session, llm, question, pool)
        route = p.tools[0] if p.tools else "sql"
        collect["plan"] = p
        collect["route"] = route

        plan_step = Step(
            kind="plan",
            label="질문 분해",
            detail=f"intent={p.intent} · tools={','.join(p.tools)}"
            + (f" · skill={p.entities['skill']}" if p.entities.get("skill") else "")
            + (" · (휴리스틱 폴백)" if plan_degraded else ""),
        )
        collect["steps"].append(plan_step)
        yield {
            "type": "plan",
            "route": route,
            "plan": {
                "intent": p.intent,
                "subqueries": p.subqueries,
                "tools": p.tools,
                "pool": p.pool,
                "entities": p.entities,
            },
        }

        tool_outputs = _dispatch(session, p)
        collect["tool_outputs"] = tool_outputs
        for o in tool_outputs:
            tr = o["tool_result"]
            step = Step(
                kind="tool",
                tool=tr["kind"] if tr["kind"] in ("graph",) else p.tools[0],
                label=tr["label"],
                detail=o["citation"]["label"],
            )
            collect["steps"].append(step)
            yield {
                "type": "step",
                "kind": "tool",
                "tool": step.tool,
                "label": step.label,
                "detail": step.detail,
            }
            yield {"type": "result", "result": tr}

        passed, eval_detail = evaluate(tool_outputs)
        eval_step = Step(kind="eval", label="근거 충분성 검증", detail=eval_detail)
        collect["steps"].append(eval_step)
        yield {"type": "step", "kind": "eval", "label": eval_step.label, "detail": eval_step.detail}

        answer, synth_degraded, answered = synthesize(llm, question, tool_outputs, passed)
        synth_step = Step(
            kind="synth",
            label="답변 합성",
            detail="LLM 폴백(템플릿)" if synth_degraded else "LLM 합성",
        )
        collect["steps"].append(synth_step)
        yield {
            "type": "step",
            "kind": "synth",
            "label": synth_step.label,
            "detail": synth_step.detail,
        }

        n = max((o.get("n", 0) for o in tool_outputs), default=0)
        # 답을 실제로 내지 못했다면(근거 자체가 없던 경우만) n과 무관하게 신뢰도를 낮춘다 —
        # '부족해요' 답변에 신뢰도 높음/N건이 함께 뜨는 모순을 막기 위함.
        confidence_level = _confidence_level(n) if answered else 0
        degraded = plan_degraded or synth_degraded or not passed or not answered

        citations = [Citation(**o["citation"]) for o in tool_outputs]
        tool_results = [ToolResult(**o["tool_result"]) for o in tool_outputs]

        collect["answer"] = answer
        collect["citations"] = citations
        collect["tool_results"] = tool_results
        collect["confidence"] = Confidence(level=confidence_level, n=n)
        collect["degraded"] = degraded

        yield {
            "type": "final",
            "answer": answer,
            "citations": [c.model_dump() for c in citations],
            "confidence": {"level": confidence_level, "n": n},
            "degraded": degraded,
        }
    except Exception as exc:  # noqa: BLE001 — SSE 계약: 실패는 error 이벤트로 알린다
        collect["exception"] = exc
        yield {"type": "error", "message": str(exc)}


def run_chat(session: Session, question: str, pool: str | None = None) -> ChatResponse:
    collect: dict[str, Any] = {}
    for _event in run_chat_events(session, question, pool, collect=collect):
        pass  # 이벤트는 스트리밍 전용 — 비스트리밍 조립은 collect로 한다

    if "exception" in collect:
        raise collect["exception"]

    return ChatResponse(
        answer=collect["answer"],
        route=collect["route"],
        plan=collect["plan"],
        steps=collect["steps"],
        tool_results=collect["tool_results"],
        citations=collect["citations"],
        confidence=collect["confidence"],
        degraded=collect["degraded"],
    )
