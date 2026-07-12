"""하이브리드 Agentic + Graph RAG 서비스 계층.

router(planner) -> tools(sql/graph/vector) -> evaluator -> synthesis 파이프라인.
정직성 원칙: 정량은 도구(SQL/graph)가 결정론적으로 답하고 LLM은 서술만 한다.
설계: docs/superpowers/specs/2026-07-10-rag-hybrid-agentic-graph-design.md
"""
