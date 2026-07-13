"""Evaluator — 검색 근거 충분성 판정.

증분 1은 결정론적: 도구가 실제 근거(n>0, 항목 존재)를 냈으면 pass.
설계상 재검색 루프(최대 2회)는 후속 증분에서 LLM 평가로 확장한다.
"""

from __future__ import annotations


def evaluate(tool_outputs: list[dict]) -> tuple[bool, str]:
    if not tool_outputs:
        return False, "근거 없음 — 도구가 결과를 반환하지 않음"
    total_n = sum(o.get("n", 0) for o in tool_outputs)
    has_items = any(o.get("tool_result", {}).get("items") for o in tool_outputs)
    if total_n <= 0 and not has_items:
        return False, "근거 표본 0 — 데이터 부족"
    return True, f"pass · 근거 표본 {total_n:,}건"
