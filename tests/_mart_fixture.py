"""load_mart 통합 테스트용 인메모리 mart/target 픽스처와 소형 taxonomy 상수."""

TAXO = {
    "_meta": {"version": "test"},
    "language": {"Python": ["python", "파이썬"], "JavaScript": ["javascript", "js"]},
    "frontend": {"React": ["react", "리액트"]},
    "_ambiguous_llm_fallback": {
        "_comment": "일반명사 충돌",
        "Go": ["go", "golang"],
        "React": ["react"],
    },
}

CERTS = {
    "_comment": "테스트용 자격증 사전",
    "국가기술자격": {"정보처리기사": ["정보처리기사", "정처기"]},
}
