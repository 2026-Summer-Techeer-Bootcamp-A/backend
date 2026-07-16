"""파이프라인 — router -> tools -> evaluator -> synthesis 오케스트레이션.

run_chat_events가 유일한 실행 경로다: SSE 계약과 정확히 같은 모양의 이벤트를 순서대로
yield하면서, 동시에 collect 딕셔너리에 조립용 원본 객체(Plan/Step/Citation/ToolResult 등)를
채운다. 스트리밍 라우트는 이벤트를 그대로 흘려보내고, 비스트리밍 run_chat은 이벤트를
소비만 하고 collect로 ChatResponse를 조립한다 — 두 경로가 로직을 공유해 드리프트가 없다.
"""

from __future__ import annotations

import time
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


def _dispatch(session: Session, p: Plan, verbose: bool = False) -> tuple[list[dict], bool]:
    """intent에 따라 도구를 실행하고 (tool_output 리스트, fell_back)을 반환.

    fell_back=True는 "질문이 겨냥한 대상과 실제로 답한 대상이 다르다"는 뜻이다 — 즉
    top_skills 랭킹으로 강제 대체됐는데, 그 대체가 직군/신입 같은 필터로도 좁혀지지
    않아 정말 근거 없이 일반 랭킹을 내놓은 경우만 True다. category/entry_level로
    스코프를 좁혀서 top_skills로 답했다면 그건 질문이 겨냥한 대상에 정확히 맞춘
    답이므로 fell_back=False로 취급한다(신뢰도가 부당하게 깎이지 않도록).
    """
    skill = p.entities.get("skill")
    category = p.entities.get("job_category")
    entry_level = bool(p.entities.get("entry_level"))
    pool = p.pool
    out: list[dict] = []

    def _run(fn: Any, *args: Any, **kwargs: Any) -> dict | None:
        """도구 함수 실행 시간을 재서 반환된 dict에 duration_ms로 얹는다(verbose 로그용)."""
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        if result is not None:
            result["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return result

    if p.intent == "cooccurrence" and skill:
        r = _run(graph_tool.co_occurring_skills, session, skill, pool, verbose=verbose)
        if r:
            out.append(r)
    elif p.intent == "semantic_search":
        r = _run(vector_tool.semantic_search, session, p.subqueries[0] if p.subqueries else "", pool, verbose=verbose)
        if r:
            out.append(r)
    elif p.intent == "skill_demand" and skill:
        r = _run(sql_tool.skill_demand, session, skill, pool, category=category, entry_level=entry_level, verbose=verbose)
        if r:
            out.append(r)
    elif p.intent == "compare":
        skills_list = p.entities.get("skills") or []
        if skills_list:
            r = _run(sql_tool.multi_skill_compare, session, list(skills_list), pool, category=category, entry_level=entry_level, verbose=verbose)
            if r:
                out.append(r)
    elif p.intent == "concept_ranking":
        out.append(_run(sql_tool.top_concepts, session, pool, verbose=verbose))
    elif p.intent == "cert_ranking":
        out.append(_run(sql_tool.top_certs, session, pool, category=category, entry_level=entry_level, verbose=verbose))
    elif p.intent == "region_distribution":
        out.append(_run(sql_tool.top_locations, session, pool, verbose=verbose))

    used_fallback_branch = not out
    if used_fallback_branch:  # 위에서 못 채웠으면(대상 미해소 등) 기술 랭킹으로 폴백
        out.append(
            _run(sql_tool.top_skills, session, pool, category=category, entry_level=entry_level, verbose=verbose)
        )
    fell_back = used_fallback_branch and not category and not entry_level

    # 교차 결합 인사이트: skill_ranking(top_skills로 답한 경우, 폴백 포함)과
    # skill_demand는 1위/특정 기술의 동반 기술을 보강 조회로 덧붙인다. compare는
    # 여러 기술을 이미 나열하므로 교차 인사이트를 붙이지 않는다.
    if p.intent in ("skill_ranking", "skill_demand") and out:
        primary_items = out[0]["tool_result"].get("items") or []
        if primary_items:
            insight_skill = primary_items[0]["name"]
            insight = _run(graph_tool.co_occurring_skills, session, insight_skill, pool, verbose=verbose)
            if insight:
                out.append(insight)

    return out, fell_back


def run_chat_events(
    session: Session,
    question: str,
    pool: str | None = None,
    *,
    verbose: bool = False,
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
        pipeline_start = time.perf_counter()
        llm = get_llm()

        plan_start = time.perf_counter()
        calls_before = llm.call_count
        p, plan_degraded = make_plan(session, llm, question, pool)
        plan_ms = round((time.perf_counter() - plan_start) * 1000, 1)
        # last_debug만 보면 이전 단계 값이 남은 건지 이번 단계에서 채워진 건지 구분이 안 되므로,
        # call_count가 실제로 늘었을 때만(=이 단계에서 LLM이 호출됐을 때만) 갖다 붙인다.
        plan_llm_debug = llm.last_debug if llm.call_count > calls_before else None

        route = p.tools[0] if p.tools else "sql"
        collect["plan"] = p
        collect["route"] = route

        plan_step = Step(
            kind="plan",
            label="질문 분해",
            detail=f"intent={p.intent} · tools={','.join(p.tools)}"
            + (f" · skill={p.entities['skill']}" if p.entities.get("skill") else "")
            + (
                f" · 직군={p.entities['job_category']}"
                if p.entities.get("job_category")
                else ""
            )
            + (" · 신입" if p.entities.get("entry_level") else "")
            + (" · (휴리스틱 폴백)" if plan_degraded else ""),
            duration_ms=plan_ms,
            debug=plan_llm_debug,
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
            "duration_ms": plan_ms,
            "debug": plan_llm_debug,
        }

        tool_outputs, fell_back = _dispatch(session, p, verbose=verbose)
        collect["tool_outputs"] = tool_outputs

        # 계획(route)과 실제로 답을 만든 도구가 다를 수 있다 — 예: semantic_search로
        # 계획했는데 임베더가 죽어 vector_tool이 None을 반환하면 _dispatch는 조용히
        # sql top_skills로 대체한다. steps에는 실제 도구(tool_outputs[0]["tool"])가
        # 정확히 찍히는데 정작 최상위 route는 계획 단계의 값이 그대로 남아, "vector로
        # 답했다"고 보고하면서 실제로는 sql로 답한 것이 되는 모순이 생겼었다. 여기서
        # route를 실제 실행된 도구로 덮어쓰고, 계획과 실제가 어긋난 경우 기존
        # fell_back(→degraded) 신호에 합류시켜 "의도한 도구가 못 쓰였다"는 사실이
        # 신뢰도/degraded에도 반영되게 한다.
        route = tool_outputs[0]["tool"] if tool_outputs else route
        fell_back = fell_back or route != collect["route"]
        collect["route"] = route

        if fell_back:
            plan_step.detail = (plan_step.detail or "") + " · (대상 미해소, 일반 랭킹으로 대체)"
        for o in tool_outputs:
            # facts는 tool_output 최상위(o["facts"])에만 있고 tool_result 안에는 없다 — synthesize()에
            # 실제로 먹인 근거 문장을 verbose 로그에서 볼 수 있게 여기서 한 번만 합쳐 넣는다.
            o["tool_result"]["facts"] = o.get("facts")
            tr = o["tool_result"]
            step = Step(
                kind="tool",
                tool=o.get("tool", p.tools[0]),
                label=tr["label"],
                detail=o["citation"]["label"],
                duration_ms=o.get("duration_ms"),
            )
            collect["steps"].append(step)
            yield {
                "type": "step",
                "kind": "tool",
                "tool": step.tool,
                "label": step.label,
                "detail": step.detail,
                "duration_ms": step.duration_ms,
            }
            yield {"type": "result", "result": tr}

        eval_start = time.perf_counter()
        passed, eval_detail = evaluate(tool_outputs)
        eval_ms = round((time.perf_counter() - eval_start) * 1000, 1)
        eval_step = Step(kind="eval", label="근거 충분성 검증", detail=eval_detail, duration_ms=eval_ms)
        collect["steps"].append(eval_step)
        yield {
            "type": "step",
            "kind": "eval",
            "label": eval_step.label,
            "detail": eval_step.detail,
            "duration_ms": eval_ms,
        }

        synth_start = time.perf_counter()
        calls_before = llm.call_count
        answer, synth_degraded, answered = synthesize(llm, question, tool_outputs, passed)
        synth_ms = round((time.perf_counter() - synth_start) * 1000, 1)
        synth_llm_debug = llm.last_debug if llm.call_count > calls_before else None
        synth_step = Step(
            kind="synth",
            label="답변 합성",
            detail="LLM 폴백(템플릿)" if synth_degraded else "LLM 합성",
            duration_ms=synth_ms,
            debug=synth_llm_debug,
        )
        collect["steps"].append(synth_step)
        yield {
            "type": "step",
            "kind": "synth",
            "label": synth_step.label,
            "detail": synth_step.detail,
            "duration_ms": synth_ms,
            "debug": synth_llm_debug,
        }

        n = max((o.get("n", 0) for o in tool_outputs), default=0)
        # 답을 실제로 내지 못했다면(근거 자체가 없던 경우만) n과 무관하게 신뢰도를 낮춘다 —
        # '부족해요' 답변에 신뢰도 높음/N건이 함께 뜨는 모순을 막기 위함.
        confidence_level = _confidence_level(n) if answered else 0
        # 의도된 도구가 대상을 못 찾아 일반 랭킹으로 강제 대체된 경우, 표본 n은 커도
        # 질문과 무관한 답이므로 신뢰도를 상한 2로 깎는다.
        if fell_back and answered:
            confidence_level = min(confidence_level, 2)

        # degraded 하나로 뭉뚱그리지 않고, 정확히 어떤 판정 때문인지 사유별로 분해한다 —
        # "근거가 얕아요" 한 줄로는 사용자가 정말 원인이 뭔지(라우팅 실패인지, 근거 부족인지,
        # LLM 합성 실패인지) 구분할 수 없었다. bool(degraded_reasons)는 기존 degraded 공식과
        # 정확히 동치라 판정 로직 자체는 바뀌지 않는다.
        degraded_reasons: list[str] = []
        if plan_degraded:
            degraded_reasons.append("의도 분류가 실패해 휴리스틱 규칙으로 대체됐어요")
        if fell_back:
            degraded_reasons.append("질문이 겨냥한 대상을 찾지 못해 일반 기술 랭킹으로 대체됐어요")
        if not passed:
            degraded_reasons.append("근거 표본이 부족해 검증을 통과하지 못했어요")
        if not answered:
            degraded_reasons.append("근거 자체가 없어 답변을 만들지 못했어요")
        if synth_degraded:
            degraded_reasons.append("LLM 합성 대신 사실을 그대로 이어붙인 답으로 대체됐어요")
        degraded = bool(degraded_reasons)

        citations = [Citation(**o["citation"]) for o in tool_outputs]
        tool_results = [ToolResult(**o["tool_result"]) for o in tool_outputs]

        collect["answer"] = answer
        collect["citations"] = citations
        collect["tool_results"] = tool_results
        collect["confidence"] = Confidence(level=confidence_level, n=n)
        collect["degraded"] = degraded
        collect["degraded_reasons"] = degraded_reasons

        total_ms = round((time.perf_counter() - pipeline_start) * 1000, 1)
        collect["total_duration_ms"] = total_ms

        yield {
            "type": "final",
            "answer": answer,
            "citations": [c.model_dump() for c in citations],
            "confidence": {"level": confidence_level, "n": n},
            "degraded": degraded,
            "degraded_reasons": degraded_reasons,
            "total_duration_ms": total_ms,
        }
    except Exception as exc:  # noqa: BLE001 — SSE 계약: 실패는 error 이벤트로 알린다
        collect["exception"] = exc
        yield {"type": "error", "message": str(exc)}


def run_chat(session: Session, question: str, pool: str | None = None, *, verbose: bool = False) -> ChatResponse:
    collect: dict[str, Any] = {}
    for _event in run_chat_events(session, question, pool, verbose=verbose, collect=collect):
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
        degraded_reasons=collect["degraded_reasons"],
        total_duration_ms=collect["total_duration_ms"],
    )
