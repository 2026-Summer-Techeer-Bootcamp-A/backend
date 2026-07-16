"""직군(position) 텍스트 -> posting_category.category ILIKE 토큰 해소.

RAG(router.py/sql_tool.py), match.py, insight.py의 position 필터가 모두 이 해소
로직을 공유한다. 클라이언트가 보내는 position 값(프론트 ResumeInsight 드롭다운의
'backend'/'frontend'/'fullstack'/'devops'/'data', RAG가 뽑아내는 '백엔드' 같은
한국어 키워드)은 mv_skill_share.position에 적재된 글로벌 영어 직무 분류
('Developer', 'Data Science', 'Sales' 등, himalayas 소스 기준)와 이름 체계가 전혀
달라서, 이 값을 그대로 정확히 일치시키면 어떤 입력을 줘도 매칭되는 행이 없다.

대신 실제 posting_category.category 값(예: '서버/백엔드 개발자', '프론트엔드 개발자',
'devops/시스템 엔지니어', '웹 풀스택 개발자')에 대한 안전한 부분 문자열(ILIKE) 토큰으로
매칭한다. 토큰은 실제 DB의 category 분포를 조회해 부분 매칭이 걸리는지 확인하고 골랐다.
"""

from __future__ import annotations

import re

# 한국어 키워드 -> posting_category.category ILIKE 토큰.
JOB_CATEGORY_KW: dict[str, str] = {
    "데이터 엔지니어": "데이터엔지니어",
    "데이터엔지니어": "데이터엔지니어",
    "데이터 사이언티스트": "데이터사이언티스트",
    "데이터사이언티스트": "데이터사이언티스트",
    "데이터 분석가": "데이터분석가",
    "데이터분석가": "데이터분석가",
    "데이터 분석": "데이터분석가",
    "백엔드": "백엔드",
    "서버 개발": "백엔드",
    "서버개발": "백엔드",
    "프론트엔드": "프론트",
    "프론트": "프론트",
    "풀스택": "풀스택",
    "머신러닝": "머신러닝",
    "인공지능": "인공지능",
    "보안": "보안",
    "게임": "게임",
    "품질": "QA",
    "데브옵스": "devops",
    "임베디드": "임베디드",
}
# 영문 짧은 토큰은 한국어 문장 속 우연한 부분 일치(예: "explain"의 "ai")를 피하려고
# 단어 경계(\b)로만 매칭한다. backend/frontend/fullstack/data는 프론트가 실제로
# 보내는 클라이언트 position 토큰이라 여기 추가했다(devops/ai/qa/dba는 RAG가 쓰던
# 원래 토큰).
JOB_CATEGORY_KW_ASCII: dict[str, str] = {
    "ai": "인공지능",
    "qa": "QA",
    "dba": "DBA",
    "devops": "devops",
    "backend": "백엔드",
    "frontend": "프론트",
    "fullstack": "풀스택",
    "data": "데이터",
}


def resolve_job_category(text_: str | None) -> str | None:
    """직군 텍스트(한국어 키워드 또는 클라이언트 영문 토큰)를 posting_category.category
    ILIKE 부분 문자열 토큰으로 해소한다.

    못 찾으면 None을 반환한다 — 호출자는 이를 "필터 없음"으로 처리해야 한다(0건으로
    단정하지 않는다). 알 수 없는 position이 조용히 전체 결과로 빠지는 것이므로,
    호출부에서 이 경우를 로그/주석으로 남겨 둔다.
    """
    if not text_:
        return None
    low = text_.lower()
    for kw, token in JOB_CATEGORY_KW.items():
        if kw in low:
            return token
    for kw, token in JOB_CATEGORY_KW_ASCII.items():
        if re.search(rf"\b{kw}\b", low):
            return token
    return None
