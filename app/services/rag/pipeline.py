"""파이프라인 — router -> tools -> evaluator -> synthesis 오케스트레이션.

run_chat_events가 유일한 실행 경로다: SSE 계약과 정확히 같은 모양의 이벤트를 순서대로
yield하면서, 동시에 collect 딕셔너리에 조립용 원본 객체(Plan/Step/Citation/ToolResult 등)를
채운다. 스트리밍 라우트는 이벤트를 그대로 흘려보내고, 비스트리밍 run_chat은 이벤트를
소비만 하고 collect로 ChatResponse를 조립한다 — 두 경로가 로직을 공유해 드리프트가 없다.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.evaluator import evaluate
from app.services.rag.llm import LLMClient, get_llm
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
from app.services.rag.tools import compare_tool, graph_tool, resume_tool, sql_tool, vector_tool


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


def _attach_single_posting_facts(session: Session, posting_id: int) -> dict | None:
    try:
        sql = (
            "SELECT p.title, p.company, p.pool, p.region_city, p.region_district, "
            "string_agg(s.canonical, ', ') as skills "
            "FROM posting p "
            "LEFT JOIN posting_tech ps ON ps.posting_id = p.id AND ps.is_deleted = false "
            "LEFT JOIN skill s ON s.id = ps.skill_id "
            "WHERE p.id = :pid "
            "GROUP BY p.id, p.title, p.company, p.pool, p.region_city, p.region_district"
        )
        row = session.execute(text(sql), {"pid": posting_id}).first()
        if not row:
            return None
        skills_str = row.skills or "기본 요구 기술"
        region_str = f" ({row.region_district or row.region_city})" if (row.region_district or row.region_city) else ""
        comp_str = f" ({row.company})" if row.company else ""
        facts_text = f"첨부 공고 '{row.title}'{comp_str}{region_str} 요구 기술 스택 — {skills_str}"
        return {
            "tool": "sql",
            "tool_result": {
                "kind": "stat",
                "label": f"'{row.title}' 공고 상세 요구사항",
                "items": [{"name": row.title, "metric": skills_str}],
            },
            "citation": {"type": "sql", "ref": f"공고 {row.title}", "label": "첨부 공고 기술 상세 분석"},
            "n": 1,
            "facts": facts_text,
        }
    except Exception:
        session.rollback()
        return None


def _dispatch(
    session: Session,
    p: Plan,
    *,
    pool: str | None = None,
    verbose: bool = False,
    owned_skill_ids: set[int] | None = None,
    posting_ids: list[int] | None = None,
    resume_text: str | None = None,
    llm: LLMClient | None = None,
) -> tuple[list[dict], bool]:
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

    # 첨부(공고/이력서)는 텍스트 인텐트보다 우선하는 명시적 신호다(K2) — 이력서와 공고를
    # 함께 첨부하거나 공고를 2개 이상 첨부한 경우는 첨부 자체가 "이걸 비교해줘"라는 뜻이
    # 명확하므로, 텍스트 인텐트가 무엇이든(LLM 실패로 overview/skill_ranking 등으로 오분류돼도)
    # 아래 텍스트 인텐트 분기보다 먼저 비교를 실행하고 조기 반환한다. 이렇게 하지 않으면
    # 인텐트가 화이트리스트({compare,resume_gap,...})를 못 맞출 때 비교가 통째로 스킵되고
    # 무관한 top_skills 랭킹으로 새어나갔다(실측 버그: "이 이력서로 이 공고에 지원하면 뭐가
    # 부족할까?"가 overview로 오분류 → 수요 상위 기술로 대체).
    #
    # 유일한 예외는 semantic_search(명시적 검색/추천)다 — 이땐 첨부를 강제로 compare로
    # 가로채지 않고, 첨부를 검색 컨텍스트로만 쓰도록 아래 텍스트 분기로 흘려보낸다.
    #
    # 대상을 못 찾아 결과가 비어도(삭제된 id 등) 여기서 top_skills로 대체하지 않는다 —
    # run_chat_events가 빈 tool_outputs를 보고 "비교 대상을 찾지 못했다"는 안내로 조기
    # 종료하므로, fell_back=False로 그대로 반환한다.
    has_resume = bool(owned_skill_ids) or bool(resume_text)
    if posting_ids and p.intent != "semantic_search" and (has_resume or len(posting_ids) >= 2):
        llm_client = llm or get_llm()
        if has_resume:
            # 이력서 ↔ 공고 N개: 공고마다 이력서 대비 커버리지/부족을 비교한다.
            # 원문 세션이 있으면 공고 수와 관계없이 각 공고를 원문 기반 LLM 딥 판정으로
            # 비교한다. 원문이 없을 때만 기존 태그 비교로 강등한다. 프론트가 여러 결과를
            # 모아 "가장 잘 맞는 공고 · 공통으로 부족한 기술"로 종합한다.
            if len(posting_ids) == 1:
                first = (
                    _run(
                        compare_tool.resume_posting_llm_compare,
                        session, resume_text, owned_skill_ids, posting_ids[0], llm_client,
                    )
                    if resume_text
                    else _run(compare_tool.resume_posting_compare, session, owned_skill_ids, posting_ids[0])
                )
                if first:
                    out.append(first)
            else:
                for pid in posting_ids:
                    r = (
                        _run(
                            compare_tool.resume_posting_llm_compare,
                            session, resume_text, owned_skill_ids, pid, llm_client,
                        )
                        if resume_text
                        else _run(compare_tool.resume_posting_compare, session, owned_skill_ids, pid)
                    )
                    if r:
                        out.append(r)
        else:
            # 공고 ↔ 공고 N개(이력서 없음): 2건이면 원문 기반 LLM 딥 판정 1쌍(기존 UX 유지),
            # 3건 이상이면 첫 공고를 기준으로 나머지와 태그 기반으로 비교한다(스타 패턴).
            # 프론트가 여러 posting_posting 결과를 모아 "모든 공고 공통 요구 · 공고별 차이"로 종합한다.
            if len(posting_ids) == 2:
                r = _run(compare_tool.posting_posting_llm_compare, session, posting_ids[0], posting_ids[1], llm_client)
                if r:
                    out.append(r)
            else:
                base = posting_ids[0]
                for other in posting_ids[1:]:
                    r = _run(compare_tool.posting_posting_compare, session, base, other)
                    if r:
                        out.append(r)
        return out, False
    if owned_skill_ids and not posting_ids and p.intent == "resume_market":
        r = _run(compare_tool.resume_market, session, owned_skill_ids, pool, category)
        if r:
            out.append(r)
        return out, False

    # 공고 1개 첨부 시 해당 공고 단독 요구 기술 및 정보 팩트 부착
    if posting_ids and len(posting_ids) == 1:
        posting_info_fact = _run(_attach_single_posting_facts, session, posting_ids[0])
        if posting_info_fact:
            out.append(posting_info_fact)

    if p.intent == "cooccurrence" and skill:
        r = _run(graph_tool.co_occurring_skills, session, skill, pool, verbose=verbose)
        if r:
            out.append(r)
    elif p.intent == "semantic_search":
        search_query = p.subqueries[0] if p.subqueries else ""
        if posting_ids:
            try:
                row = session.execute(text("SELECT title FROM posting WHERE id = :pid"), {"pid": posting_ids[0]}).first()
                if row and row.title:
                    clean_q = re.sub(r'(이거|이\s*공고|이것|해당\s*공고)(랑|와|의|등|들)?', '', search_query).strip()
                    search_query = f"{row.title} {clean_q}".strip()
            except Exception:
                session.rollback()
                pass
        r = _run(vector_tool.semantic_search, session, search_query, pool, verbose=verbose)
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
        out.append(_run(sql_tool.top_locations, session, pool, category=category, verbose=verbose))
    elif p.intent == "resume_gap":
        r = _run(resume_tool.resume_gap, session, owned_skill_ids, pool, category=category)
        if r:
            out.append(r)
    elif p.intent == "resume_coverage":
        r = _run(resume_tool.resume_coverage, session, owned_skill_ids, pool, category=category)
        if r:
            out.append(r)
    elif p.intent == "resume_recommend":
        region = p.entities.get("region")
        r = _run(resume_tool.resume_recommend, session, owned_skill_ids, pool, region=region)
        if r:
            out.append(r)
    elif p.intent == "skill_ranking":
        # 이전에는 skill_ranking 전용 분기가 없어 모든 "상위 기술" 질문이 아래 폴백
        # 분기로 떨어졌다 — 그 결과 fell_back=True로 오판되어 정상 랭킹 질문인데도
        # 신뢰도가 상한 2로 깎이고 "대체됨"으로 표시되는 버그가 있었다. 실제 겨냥한
        # intent를 그대로 answering하도록 명시적으로 분기한다.
        r = _run(sql_tool.top_skills, session, pool, category=category, entry_level=entry_level, verbose=verbose)
        if r:
            out.append(r)

    # semantic_search, compare, resume_recommend 등 특정 대상 탐색/비교 인텐트는
    # 도구가 결과를 내지 못했을 때 무관한 top_skills 랭킹으로 덮어쓰지 않는다(K2).
    # 엉뚱한 Python/JS 랭킹 템플릿 출식을 막기 위함이다.
    no_fallback_intents = {"semantic_search", "compare", "resume_recommend", "resume_gap", "resume_coverage"}
    used_fallback_branch = not out and p.intent not in no_fallback_intents
    if used_fallback_branch:  # 일반 요약/랭킹 질문에서만 미해소 시 기술 랭킹으로 폴백
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
    owned_skill_ids: set[int] | None = None,
    posting_ids: list[int] | None = None,
    resume_text: str | None = None,
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

        # 이력서 기준 질문인데 첨부된 이력서(owned_skill_ids)가 없으면 도구를 실행하지도,
        # 일반 랭킹으로 대체하지도 않는다 — 이력서 없이 "부족한 스킬"/"시장 적합도"를
        # 답하면 근거 없는 거짓 답이 되기 때문이다. 여기서 바로 안내 문구로 조기 종료한다.
        # resume_market도 resume_gap/resume_coverage와 마찬가지로 이력서 없이는 성립할
        # 수 없는 질문이라 같은 취급을 한다(_dispatch의 세 번째 분기도 owned_skill_ids가
        # 없으면 애초에 안 타므로, 여기서 걸러주지 않으면 무관한 top_skills로 새어나간다).
        # 단, 세션 범위 이력서 원문(resume_text)과 공고 한 개가 함께 온 경우는 예외다 —
        # 이때는 owned_skill_ids(저장된 이력서의 스킬 태그)가 비어 있어도 _dispatch의
        # resume_posting_llm_compare 분기(첨부가 텍스트 인텐트보다 우선한다는 설계,
        # 위 _dispatch의 첫 주석 블록 참고)가 원문을 직접 읽고 판정하므로 도구 실행
        # 전에 조기 종료하면 오히려 정상 요청을 막게 된다.
        if (
            p.intent in ("resume_gap", "resume_coverage", "resume_market", "resume_recommend")
            and not owned_skill_ids
            and not (resume_text and posting_ids and len(posting_ids) == 1)
        ):
            answer = (
                "이력서를 먼저 첨부해 주세요. 첨부하면 이력서 기준으로 부족한 기술과 "
                "지원 가능한 공고를 분석해 드려요."
            )
            degraded_reasons = ["이력서가 첨부되지 않아 이력서 기준 분석을 할 수 없어요"]
            total_ms = round((time.perf_counter() - pipeline_start) * 1000, 1)
            collect["answer"] = answer
            collect["citations"] = []
            collect["tool_results"] = []
            collect["confidence"] = Confidence(level=0, n=0)
            collect["degraded"] = True
            collect["degraded_reasons"] = degraded_reasons
            collect["total_duration_ms"] = total_ms
            yield {
                "type": "final",
                "answer": answer,
                "citations": [],
                "confidence": {"level": 0, "n": 0},
                "degraded": True,
                "degraded_reasons": degraded_reasons,
                "total_duration_ms": total_ms,
            }
            return

        tool_outputs, fell_back = _dispatch(
            session,
            p,
            verbose=verbose,
            owned_skill_ids=owned_skill_ids,
            posting_ids=posting_ids,
            resume_text=resume_text,
            llm=llm,
        )
        collect["tool_outputs"] = tool_outputs

        # 첨부 기반 비교(공고 2개 비교, 이력서-공고 비교)를 시도했는데 대상 공고를 찾지
        # 못하면(삭제됐거나 잘못된 id) _dispatch는 top_skills로 대체하지 않고 빈 채로
        # 반환한다 — 여기서도 그 빈 상태를 일반 랭킹/합성으로 흘려보내지 않고 바로
        # 안내 문구로 조기 종료한다(위 이력서 미첨부 조기 종료와 동일한 스타일).
        if not tool_outputs and posting_ids:
            answer = "비교할 공고를 찾지 못했어요. 첨부한 공고 정보를 다시 확인해 주세요."
            degraded_reasons = ["첨부한 공고를 찾지 못해 비교 결과를 만들 수 없어요"]
            total_ms = round((time.perf_counter() - pipeline_start) * 1000, 1)
            collect["answer"] = answer
            collect["citations"] = []
            collect["tool_results"] = []
            collect["confidence"] = Confidence(level=0, n=0)
            collect["degraded"] = True
            collect["degraded_reasons"] = degraded_reasons
            collect["total_duration_ms"] = total_ms
            yield {
                "type": "final",
                "answer": answer,
                "citations": [],
                "confidence": {"level": 0, "n": 0},
                "degraded": True,
                "degraded_reasons": degraded_reasons,
                "total_duration_ms": total_ms,
            }
            return

        # 계획(route)과 실제로 답을 만든 도구가 다를 수 있다 — 예: semantic_search로
        # 계획했는데 임베더가 죽어 vector_tool이 None을 반환하면 _dispatch는 조용히
        # sql top_skills로 대체한다. steps에는 실제 도구(tool_outputs[0]["tool"])가
        # 정확히 찍히는데 정작 최상위 route는 계획 단계의 값이 그대로 남아, "vector로
        # 답했다"고 보고하면서 실제로는 sql로 답한 것이 되는 모순이 생겼었다. 여기서
        # route를 실제 실행된 도구로 덮어쓰고, 계획과 실제가 어긋난 경우 기존
        # fell_back(→degraded) 신호에 합류시켜 "의도한 도구가 못 쓰였다"는 사실이
        # 신뢰도/degraded에도 반영되게 한다.
        #
        # 다만 posting_ids(첨부 공고)가 있는 경우는 예외다 — 이력서 텍스트 인텐트(예:
        # resume_coverage)로 계획됐어도 _dispatch가 첨부 우선 설계(K2)에 따라 compare
        # 도구로 정당하게 갈아탄다(위 elif resume_text and posting_ids 분기). 이건
        # "대상을 못 찾아 대체된" 상황이 아니라 첨부가 의도한 그대로 응답한 것이므로,
        # posting_ids가 있으면 route 불일치를 fell_back으로 치지 않는다.
        route = tool_outputs[0]["tool"] if tool_outputs else route
        fell_back = fell_back or (route != collect["route"] and not posting_ids)
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


def run_chat(
    session: Session,
    question: str,
    pool: str | None = None,
    *,
    verbose: bool = False,
    owned_skill_ids: set[int] | None = None,
    posting_ids: list[int] | None = None,
    resume_text: str | None = None,
) -> ChatResponse:
    collect: dict[str, Any] = {}
    for _event in run_chat_events(
        session,
        question,
        pool,
        verbose=verbose,
        collect=collect,
        owned_skill_ids=owned_skill_ids,
        posting_ids=posting_ids,
        resume_text=resume_text,
    ):
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
