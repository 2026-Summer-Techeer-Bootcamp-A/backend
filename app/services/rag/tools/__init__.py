"""RAG 도구 계층. 정량·관계 질문에 결정론적으로(SQL) 답한다.

각 도구는 {tool_result, citation, n, facts} 구조를 반환한다.
- tool_result: 프론트 렌더용 ToolResult
- citation: 근거 인용
- n: 근거 표본 수(confidence 산정)
- facts: synthesis LLM에 넘길 사실 요약(문자열)
"""
