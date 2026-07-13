"""파이프라인 — router -> tools -> evaluator -> synthesis 오케스트레이션.

단계마다 steps[]에 기록해 프론트가 'plan->tool->eval->synth' 흐름을 렌더한다.
"""

from __future__ import annotations

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
from app.services.rag.tools import graph_tool, sql_tool


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
    elif p.intent == "skill_demand" and skill:
        r = sql_tool.skill_demand(session, skill, pool)
        if r:
            out.append(r)
    elif p.intent == "concept_ranking":
        out.append(sql_tool.top_concepts(session, pool))
    elif p.intent == "cert_ranking":
        out.append(sql_tool.top_certs(session, pool))

    if not out:  # 위에서 못 채웠으면(대상 미해소 등) 기술 랭킹으로 폴백
        out.append(sql_tool.top_skills(session, pool))
    return out


def run_chat(session: Session, question: str, pool: str | None = None) -> ChatResponse:
    llm = get_llm()
    steps: list[Step] = []

    p, plan_degraded = make_plan(session, llm, question, pool)
    steps.append(
        Step(
            kind="plan",
            label="질문 분해",
            detail=f"intent={p.intent} · tools={','.join(p.tools)}"
            + (f" · skill={p.entities['skill']}" if p.entities.get("skill") else "")
            + (" · (휴리스틱 폴백)" if plan_degraded else ""),
        )
    )

    tool_outputs = _dispatch(session, p)
    for o in tool_outputs:
        tr = o["tool_result"]
        steps.append(
            Step(
                kind="tool",
                tool=tr["kind"] if tr["kind"] in ("graph",) else p.tools[0],
                label=tr["label"],
                detail=o["citation"]["label"],
            )
        )

    passed, eval_detail = evaluate(tool_outputs)
    steps.append(Step(kind="eval", label="근거 충분성 검증", detail=eval_detail))

    answer, synth_degraded = synthesize(llm, question, tool_outputs, passed)
    steps.append(
        Step(kind="synth", label="답변 합성", detail="LLM 폴백(템플릿)" if synth_degraded else "LLM 합성")
    )

    n = max((o.get("n", 0) for o in tool_outputs), default=0)
    route = tool_outputs[0]["tool_result"]["kind"] if tool_outputs else "none"
    if route not in ("graph",):
        route = p.tools[0] if p.tools else "sql"

    return ChatResponse(
        answer=answer,
        route=route,
        plan=p,
        steps=steps,
        tool_results=[ToolResult(**o["tool_result"]) for o in tool_outputs],
        citations=[Citation(**o["citation"]) for o in tool_outputs],
        confidence=Confidence(level=_confidence_level(n), n=n),
        degraded=plan_degraded or synth_degraded or not passed,
    )
