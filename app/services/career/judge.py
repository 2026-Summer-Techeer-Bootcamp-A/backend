"""요구별 판정 대상 문서(이력서 또는 공고) 판정과 할루시네이션 가드, 가중 점수.

LLM이 met/partial로 판정하면서 판정 대상 문서에 실제로 없는 문장을 인용하면 화면에
그대로 노출되지 않도록, 인용이 판정 대상 원문에 부분문자열로 존재하는지
정규화 후 문자열 검사로 확인한다. 존재하지 않으면 gap으로 강등한다.

target_label로 판정 대상이 무엇인지("이력서" 기본값, 공고 대 공고 비교면 "비교 대상
공고" 등)를 프롬프트에 실어, 이력서/공고 어느 쪽이든 같은 판정 로직을 재사용한다.
"""

from __future__ import annotations

import re
from typing import TypedDict

from app.services.career.requirements import Requirement
from app.services.rag.llm import LLMClient


class Judgment(TypedDict):
    req_id: str
    verdict: str
    quote: str
    rationale: str
    next_step: str


_VALID = {"met", "partial", "gap"}


def _system_prompt(target_label: str) -> str:
    return (
        f"너는 {target_label}가 공고 요구를 충족하는지 판정하는 분석기다. 요구마다 met(충족), "
        "partial(전이 가능한 인접 경험), gap(근거 없음) 중 하나를 매긴다. met과 partial은 "
        f"{target_label} 원문에서 근거 문장을 반드시 그대로 인용한다. 지어내지 않는다. gap이면 한 문장으로 "
        "보완 제안을 next_step에 담는다. JSON 객체 하나만 출력한다."
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def judge_requirements(
    requirements: list[Requirement],
    resume_text: str,
    llm: LLMClient,
    target_label: str = "이력서",
) -> tuple[list[Judgment], bool]:
    """요구사항마다 판정 대상 원문(resume_text) 대비 met/partial/gap을 판정한다.

    target_label은 resume_text가 실제로 무엇인지(이력서 원문 또는 비교 대상 공고
    원문)를 프롬프트에 실어주는 라벨이다. 기본값 "이력서"는 기존 이력서 대 공고
    비교와 동일하게 동작한다.

    반환하는 bool(llm_ok)은 LLM이 실제로 판정을 만들어냈는지를 가리킨다. 아래 루프
    뒤에서 판정이 누락된 요구를 전부 gap으로 채우기 때문에, LLM이 완전히 실패해도
    (items가 리스트가 아니거나 요구 id가 하나도 안 맞아도) 반환 리스트 자체는 항상
    비어있지 않다 — llm_ok 없이는 호출부가 이걸 "판정 성공"으로 오인해 근거 없는
    전부-gap 결과에 degraded=False를 붙이게 된다. llm_ok는 모델 응답에서 실제 요구
    id와 매칭된 항목이 하나라도 있었을 때만 True다(기본 gap 채움은 포함하지 않는다).
    """
    if not requirements:
        return [], False
    req_lines = "\n".join(f'{r["id"]}: {r["text"]}' for r in requirements)
    prompt = (
        f"{target_label} 원문:\n{resume_text}\n\n요구사항:\n{req_lines}\n\n"
        'items 배열로 답한다. 각 원소는 {"req_id":"R1","verdict":"met|partial|gap",'
        f'"quote":"{target_label} 원문 인용","rationale":"판정 근거 한 줄",'
        '"next_step":"gap일 때 보완 제안"} 형식이다.'
    )
    out = llm.json(_system_prompt(target_label), prompt, temperature=0.2, max_output_tokens=2048)
    items = (out or {}).get("items")
    norm_resume = _norm(resume_text)
    by_id = {r["id"]: r for r in requirements}
    judged: dict[str, Judgment] = {}
    llm_ok = False
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            rid = str(it.get("req_id") or "")
            if rid not in by_id:
                continue
            verdict = str(it.get("verdict") or "gap")
            if verdict not in _VALID:
                verdict = "gap"
            quote = str(it.get("quote") or "")
            # 할루시네이션 가드: 인용이 원문에 없으면 gap으로 강등한다.
            if verdict in ("met", "partial") and (not quote or _norm(quote) not in norm_resume):
                verdict, quote = "gap", ""
            judged[rid] = {
                "req_id": rid,
                "verdict": verdict,
                "quote": quote,
                "rationale": str(it.get("rationale") or ""),
                "next_step": str(it.get("next_step") or ""),
            }
            # 할루시네이션 가드로 gap 강등됐더라도 모델이 이 요구를 실제로 판정한
            # 것이므로 llm_ok는 True다 — False는 오직 아래 기본 gap 채움뿐이다.
            llm_ok = True
    # 판정이 누락된 요구는 gap으로 채운다(항상 요구 수만큼 반환).
    for r in requirements:
        judged.setdefault(
            r["id"],
            {
                "req_id": r["id"],
                "verdict": "gap",
                "quote": "",
                "rationale": "",
                "next_step": "",
            },
        )
    return [judged[r["id"]] for r in requirements], llm_ok


_WEIGHT = {"met": 1.0, "partial": 0.5, "gap": 0.0}


def weighted_score(judgments: list[Judgment]) -> float:
    if not judgments:
        return 0.0
    total = sum(_WEIGHT.get(j["verdict"], 0.0) for j in judgments)
    return round(total / len(judgments) * 100, 1)
