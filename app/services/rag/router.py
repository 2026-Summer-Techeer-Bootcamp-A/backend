"""Router/Planner — 질문을 분해해 intent·tools·entities를 정한다.

LLM(Gemini)로 계획을 뽑되, 실패하면 키워드 휴리스틱으로 폴백(degraded).
정직성: 정량·랭킹 intent는 무조건 sql, 관계 질문만 graph.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.job_category import resolve_job_category
from app.services.rag.llm import LLMClient
from app.services.rag.schemas import Plan

# intent -> 사용할 도구
INTENT_TOOLS = {
    "cooccurrence": ["graph"],
    "skill_demand": ["sql"],
    "skill_ranking": ["sql"],
    "compare": ["sql"],
    "concept_ranking": ["sql"],
    "cert_ranking": ["sql"],
    "semantic_search": ["vector"],
    "overview": ["sql"],
    "region_distribution": ["sql"],
    "resume_gap": ["resume"],
    "resume_coverage": ["resume"],
    # K2 버그 수정: 이전에는 이력서가 첨부돼 있으면 텍스트 인텐트와 무관하게 무조건
    # resume_market으로 가로챘다(pipeline._dispatch 세 번째 분기) — "React 수요 어때?"
    # 처럼 이력서와 무관한 질문에도 이력서-시장 비교로 답해버리는 오탐이었다. 이제
    # resume_market은 텍스트가 실제로 "내 이력서를 시장과 비교/분석/평가해줘" 류를
    # 가리킬 때만 명시적으로 분류되는 인텐트다 — 첨부된 이력서는 더 이상 인텐트를
    # 덮어쓰지 않고, resume_market으로 분류됐을 때만 컨텍스트로 쓰인다.
    "resume_market": ["resume"],
    # K3: "넣어볼만한 공고 추천해줘" 류 — resume_coverage(커버리지 %/지원가능 건수)나
    # resume_gap(부족 스킬 목록)과 달리 실제로 지원해볼 만한 "구체적인 공고 목록"을
    # 원하는 요청이다. 프론트가 카드로 렌더링할 수 있도록 posting id를 들고 다닌다.
    "resume_recommend": ["resume"],
}

_COOCCUR_KW = ("같이", "함께", "동반", "궁합", "짝", "with", "together", "pair", "combo")
_COMPARE_KW = ("비교", "차이", "vs", "versus", "compare", "어느 게", "어떤 게", "뭐가 더", "뭐가 낫", "어떤 것이")
_SEMANTIC_KW = ("찾아", "추천", "비슷", "유사", "관련 공고", "같은 공고", "어떤 공고", "공고 있", "recommend", "similar")
_CONCEPT_KW = ("개념", "패러다임", "트렌드", "msa", "마이크로서비스", "생성형", "대규모", "아키텍처", "devops", "ci/cd")
_CERT_KW = ("자격증", "자격", "cert", "토익", "정보처리")
_RANK_KW = ("순위", "많이", "상위", "top", "인기", "가장", "수요")
_REGION_KW = ("어디", "위치", "지역", "몰려", "밀집")
# 사용자 본인 이력서를 가리키는 강한 신호 — 이 키워드가 있으면 다른 인텐트보다 우선해
# resume_gap/resume_coverage로 분류한다(첨부된 이력서 없이는 pipeline이 조기 안내한다).
_RESUME_STRONG_KW = ("내 이력서", "제 이력서", "내 커버리지", "제 커버리지")
# 단독으로도 이력서 갭/커버리지 질문임이 분명한 키워드.
_RESUME_GAP_STANDALONE_KW = ("부족한 스킬", "부족한 기술", "뭘 배워야", "모자란")
_RESUME_COVERAGE_STANDALONE_KW = ("지원 가능", "지원할 수 있", "갈 수 있는 공고")
# "얼마나"/"커버리지" 등은 흔한 일반 단어라(예: "이 기술 요구 공고 얼마나 있어?") 단독으로는
# 오탐이 나기 쉽다 — _RESUME_STRONG_KW로 본인 이력서 언급이 확인된 문장에서만 보조 신호로 쓴다.
_RESUME_GAP_COMBO_KW = ("부족", "갭")
_RESUME_COVERAGE_COMBO_KW = ("커버리지", "맞는 공고", "얼마나")
# resume_market 전용 본인-이력서 신호. gap/coverage용 _RESUME_STRONG_KW보다 넓게
# "내 경쟁력"/"내 수준"까지 포함한다 — "내 경쟁력 어때?"처럼 이력서라는 단어 없이도
# 본인 역량을 시장과 견주는 질문이 흔하기 때문이다.
_RESUME_MARKET_REF_KW = ("내 이력서", "제 이력서", "내 경쟁력", "내 수준")
# 시장/분석 계열 단어 — _RESUME_MARKET_REF_KW와 결합했을 때만 resume_market으로 분류한다
# (단독으로는 "React 시장 어때?"처럼 이력서와 무관한 질문에도 흔히 등장해 오탐이 나기 쉽다).
_RESUME_MARKET_COMBO_KW = ("분석", "평가", "어때", "적합", "시장", "비교", "경쟁력", "수준")
# 단독으로도 "본인 이력서를 시장과 견준다"는 의미가 분명한 문구.
_RESUME_MARKET_STANDALONE_KW = ("시장 적합도", "내 경쟁력")
# resume_recommend: "구체적인 공고를 추천/제안해달라"는 행위 동사. resume_coverage의
# _RESUME_COVERAGE_COMBO_KW와 "맞는 공고"가 겹치지만, 여기서는 항상 아래 REF_KW(이력서를
# 가리키는 넓은 지칭)와 결합됐을 때만 트리거하는 combo 전용 패턴이라 오탐 폭이 좁다.
_RESUME_RECOMMEND_ACTION_KW = ("넣어볼만한", "지원할만한", "지원해볼", "어울리는 공고", "맞는 공고", "추천")
# _RESUME_STRONG_KW("내 이력서"/"제 이력서")보다 넓게 "이력서"라는 단어 자체(예: "이 이력서로")와
# "내 스킬"/"내 기술"까지 포함한다 — "이 이력서로 넣어볼만한 공고 추천해줘"처럼 "내"가 아니라
# "이"로 지칭하는 문장도 포착해야 하기 때문이다.
_RESUME_RECOMMEND_REF_KW = ("이력서", "내 스킬", "내 기술")

# 지역 토큰 -> posting.region_city/region_district에 대한 안전한 ILIKE 부분 문자열.
# 더 구체적인(구/동 단위) 토큰을 먼저 두어 "서울 강남" 같은 문장에서 더 좁은 지역이
# 우선 매칭되게 한다. 매칭되는 토큰이 없으면 None(필터 없음 — 0건으로 단정하지 않는다).
_REGION_TOKENS: dict[str, str] = {
    "강남": "강남",
    "판교": "판교",
    "분당": "분당",
    "성남": "성남",
    "여의도": "여의도",
    "잠실": "잠실",
    "구로": "구로",
    "마포": "마포",
    "종로": "종로",
    "서울": "서울",
    "인천": "인천",
    "부산": "부산",
    "대구": "대구",
    "대전": "대전",
    "광주": "광주",
}

_ENTRY_LEVEL_KW = (
    "신입",
    "주니어",
    "경력무관",
    "경력 무관",
    "0년",
    "junior",
    "entry level",
    "entry-level",
)

_PLANNER_SYSTEM = (
    "You are a query planner for a Korean job-market RAG. "
    "Classify the user question into exactly one intent and extract entities. "
    "Return ONLY JSON: {\"intent\": one of "
    "[cooccurrence, skill_demand, skill_ranking, compare, concept_ranking, cert_ranking, "
    "semantic_search, overview, region_distribution, resume_gap, resume_coverage, resume_market, "
    "resume_recommend], "
    "\"skill\": <a single tech name mentioned or null>, "
    "\"skills\": <list of tech names when comparing multiple techs, else []>, "
    "\"pool\": <domestic|global|null>, "
    "\"job_category\": <a single job-function keyword mentioned or null>, "
    "\"entry_level\": <true|null>}. "
    "cooccurrence = which techs go together with X. "
    "skill_demand = how many postings want X. "
    "skill_ranking = most demanded techs. concept_ranking = paradigms/concepts. "
    "compare = compare demand/trend of MULTIPLE named techs side by side (e.g. React vs Vue vs Angular). "
    "cert_ranking = certifications. "
    "semantic_search = find/recommend postings similar to a free-form description. "
    "region_distribution = where postings are concentrated geographically (region/location). "
    "resume_gap = the question explicitly references the user's OWN resume/skills "
    "(e.g. 내 이력서, 제 이력서) and asks what skills THEY are missing/lacking compared to "
    "market demand (e.g. 내 이력서 기준 부족한 스킬 뭐야, 부족한 기술이 뭐야). "
    "resume_coverage = the question explicitly references the user's OWN resume "
    "(e.g. 내 이력서, 제 이력서, 내 커버리지) and asks how many postings they could apply to, "
    "or their skill coverage/match rate against the market (e.g. 내 이력서로 지원 가능한 공고 "
    "얼마나 돼, 내 커버리지 어때). If the question clearly references the user's own resume "
    "but it's ambiguous whether they want gap or coverage, classify as resume_coverage. "
    "Only use resume_gap/resume_coverage when the question is about the user's OWN resume, "
    "never for generic market questions. "
    "resume_market = the question asks to analyze/evaluate the user's OWN resume against "
    "the market as a whole (e.g. 내 이력서를 시장과 비교해줘, 내 경쟁력 어때, 내 이력서 시장 "
    "적합도 어때) rather than asking specifically for a gap list (resume_gap) or an apply-count "
    "(resume_coverage). Only use resume_market when the question explicitly references the "
    "user's OWN resume/competitiveness, never for generic market questions like '리액트 수요 "
    "어때' even if a resume happens to be attached. "
    "resume_recommend = the question explicitly references the user's OWN resume/skills and "
    "asks to RECOMMEND or FIND specific job postings that fit them, expecting an actual LIST of "
    "postings back (e.g. 이 이력서로 넣어볼만한 공고 추천해줘, 내 스킬에 맞는 공고 추천해줘, "
    "지원할만한 공고 찾아줘). This is different from resume_coverage, which answers with a "
    "coverage percentage or an apply-count STAT, not a list of named postings — use "
    "resume_recommend whenever the user wants concrete postings suggested, not a number. "
    "overview = general market summary. "
    "job_category = the job function/role the question targets (e.g. backend, frontend, "
    "data engineer, data scientist, data analyst, machine learning, AI, security, game, QA, "
    "DBA, devops, embedded), extracted verbatim from the question if mentioned, else null. "
    "entry_level = true if the question targets entry-level/junior/no-experience-required "
    "candidates (e.g. 신입, 주니어, 경력무관), else null."
)


def _detect_skill(session: Session, q: str) -> str | None:
    """폴백용: 질문에 등장하는 가장 긴 기술 정규명(2자 이상)을 찾는다."""
    row = session.execute(
        text(
            "SELECT canonical FROM skill "
            "WHERE length(canonical) >= 2 AND :q ILIKE '%' || canonical || '%' "
            "ORDER BY length(canonical) DESC LIMIT 1"
        ),
        {"q": q},
    ).first()
    return row.canonical if row else None


def _detect_skills_multi(session: Session, q: str) -> list[str]:
    """질문에 등장하는 기술 정규명을 여러 개 추출한다 (비교 쿼리용, 최대 5개)."""
    rows = session.execute(
        text(
            "SELECT canonical FROM skill "
            "WHERE length(canonical) >= 2 AND :q ILIKE '%' || canonical || '%' "
            "ORDER BY length(canonical) DESC LIMIT 5"
        ),
        {"q": q},
    ).all()
    return [r.canonical for r in rows]


def _detect_job_category(text_: str | None) -> str | None:
    """질문(혹은 LLM이 뽑은 후보 문자열)에서 안전한 ILIKE 부분 문자열 토큰을 찾는다.

    실제 해소 로직은 app.services.job_category(resolve_job_category)로 옮겼다 —
    match.py/insight.py의 position 필터도 같은 표를 써야 backend/frontend 같은
    직군 토큰이 surface마다 다르게 해석되는 일이 없기 때문이다.
    """
    return resolve_job_category(text_)


def _detect_region(text_: str | None) -> str | None:
    """질문에 등장하는 지역 토큰을 찾는다(_REGION_TOKENS 참고). LLM 없이 순수 텍스트
    매칭이라 heuristic/LLM 플랜 경로 양쪽에서 그대로 재사용한다."""
    if not text_:
        return None
    for kw, token in _REGION_TOKENS.items():
        if kw in text_:
            return token
    return None


def _detect_entry_level(text_: str | None) -> bool:
    if not text_:
        return False
    low = text_.lower()
    return any(k in low for k in _ENTRY_LEVEL_KW)


def _build_entities(
    skill: str | None, job_category: str | None, entry_level: bool
) -> dict[str, object]:
    entities: dict[str, object] = {}
    if skill:
        entities["skill"] = skill
    if job_category:
        entities["job_category"] = job_category
    if entry_level:
        entities["entry_level"] = True
    return entities


def _heuristic(session: Session, q: str, pool: str | None) -> Plan:
    low = q.lower()
    skill = _detect_skill(session, q)
    job_category = _detect_job_category(q)
    entry_level = _detect_entry_level(q)

    # "내 이력서"/"제 커버리지" 같은 강한 본인-이력서 신호가 있거나 부족/갭·커버리지
    # 키워드가 있으면, 다른 인텐트보다 우선해 resume_gap/resume_coverage/resume_market으로
    # 분류한다. 구체적인 신호(부족한 스킬 목록 vs 지원 가능 목록 vs 시장 비교/분석)부터
    # 순서대로 확인하고, 그 무엇에도 안 걸리면(강한 신호만 있는 포괄적 질문) coverage를
    # 기본값으로 둔다 — "내 이력서 어때?" 류의 질문은 "지원 가능 범위"로 답하는 편이 더
    # 유용하기 때문이다.
    has_resume_ref = any(k in q for k in _RESUME_STRONG_KW)
    has_gap_standalone = any(k in low for k in _RESUME_GAP_STANDALONE_KW)
    has_coverage_standalone = any(k in low for k in _RESUME_COVERAGE_STANDALONE_KW)
    has_gap_combo = has_resume_ref and any(k in low for k in _RESUME_GAP_COMBO_KW)
    has_coverage_combo = has_resume_ref and any(k in low for k in _RESUME_COVERAGE_COMBO_KW)
    # resume_market: "내 이력서"/"제 이력서"/"내 경쟁력"/"내 수준" 같은 본인-이력서 신호가
    # 시장/분석 계열 단어와 결합됐거나, 그 자체로 명확한 단독 문구일 때만 분류한다 —
    # 이력서가 첨부돼 있다는 사실만으로는 절대 트리거하지 않는다(그건 pipeline._dispatch가
    # p.intent == "resume_market"일 때만 첨부를 컨텍스트로 쓰도록 별도로 보장한다).
    has_market_ref = any(k in q for k in _RESUME_MARKET_REF_KW)
    has_market_combo = has_market_ref and any(k in low for k in _RESUME_MARKET_COMBO_KW)
    has_market_standalone = any(k in q for k in _RESUME_MARKET_STANDALONE_KW)
    # resume_recommend는 "추천해줘"류 행위 동사 + 이력서 지칭이 함께 있을 때만 분류한다
    # (combo 전용, standalone 없음) — 다른 resume 분기보다 먼저 확인해 "맞는 공고"/"추천"이
    # resume_coverage 콤보 키워드와 겹치는 경우에도 실제로 "목록을 달라"는 요청 쪽을 우선시한다.
    has_recommend_ref = any(k in q for k in _RESUME_RECOMMEND_REF_KW)
    has_recommend_action = any(k in q for k in _RESUME_RECOMMEND_ACTION_KW)
    if has_recommend_ref and has_recommend_action:
        intent = "resume_recommend"
        entities = _build_entities(skill, job_category, entry_level)
        region = _detect_region(q)
        if region:
            entities["region"] = region
        return Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool,
            entities=entities,
            subqueries=[q],
        )
    if has_gap_standalone or has_gap_combo:
        intent = "resume_gap"
        return Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool,
            entities=_build_entities(skill, job_category, entry_level),
            subqueries=[q],
        )
    if has_coverage_standalone or has_coverage_combo:
        intent = "resume_coverage"
        return Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool,
            entities=_build_entities(skill, job_category, entry_level),
            subqueries=[q],
        )
    if has_market_standalone or has_market_combo:
        intent = "resume_market"
        return Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool,
            entities=_build_entities(skill, job_category, entry_level),
            subqueries=[q],
        )
    if has_resume_ref:
        intent = "resume_coverage"
        return Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool,
            entities=_build_entities(skill, job_category, entry_level),
            subqueries=[q],
        )

    if any(k in low for k in _COMPARE_KW):
        skills_multi = _detect_skills_multi(session, q)
        if len(skills_multi) >= 2:
            return Plan(
                intent="compare",
                tools=INTENT_TOOLS["compare"],
                pool=pool,
                entities={"skills": skills_multi, **_build_entities(None, job_category, entry_level)},
                subqueries=[q],
            )
    if skill and any(k in low for k in _COOCCUR_KW):
        intent = "cooccurrence"
    elif any(k in q for k in _SEMANTIC_KW):
        intent = "semantic_search"
    elif any(k in low for k in _CERT_KW):
        intent = "cert_ranking"
    elif any(k in low for k in _CONCEPT_KW):
        intent = "concept_ranking"
    elif any(k in low for k in _REGION_KW):
        intent = "region_distribution"
    elif skill:
        intent = "skill_demand"
    elif any(k in low for k in _RANK_KW):
        intent = "skill_ranking"
    else:
        intent = "overview"
    return Plan(
        intent=intent,
        tools=INTENT_TOOLS[intent],
        pool=pool,
        entities=_build_entities(skill, job_category, entry_level),
        subqueries=[q],
    )


def plan(session: Session, llm: LLMClient, question: str, pool: str | None) -> tuple[Plan, bool]:
    """(Plan, degraded). LLM 성공 시 degraded=False, 폴백 시 True."""
    raw = llm.json(_PLANNER_SYSTEM, question, temperature=0.0)
    if not raw or raw.get("intent") not in INTENT_TOOLS:
        return _heuristic(session, question, pool), True

    intent = raw["intent"]
    skill = raw.get("skill") or None
    # compare intent: LLM이 skills 리스트를 뽑았거나, 없으면 질문에서 직접 탐지
    skills_multi: list[str] = raw.get("skills") or []
    if intent == "compare":
        if len(skills_multi) < 2:
            skills_multi = _detect_skills_multi(session, question)
        if len(skills_multi) < 2:
            # 비교 대상을 2개 이상 못 찾으면 skill_demand로 강등
            intent = "skill_demand"
    llm_pool = raw.get("pool") if raw.get("pool") in ("domestic", "global") else None
    job_category = raw.get("job_category")
    job_category = _detect_job_category(job_category if isinstance(job_category, str) else None)
    if not job_category:
        job_category = _detect_job_category(question)
    entry_level = bool(raw.get("entry_level")) or _detect_entry_level(question)
    if intent in ("cooccurrence", "skill_demand") and not skill:
        skill = _detect_skill(session, question)
        if not skill:
            intent = "skill_ranking"
    entities = _build_entities(skill, job_category, entry_level)
    if intent == "compare" and skills_multi:
        entities["skills"] = skills_multi
    if intent == "resume_recommend":
        region = _detect_region(question)
        if region:
            entities["region"] = region
    return (
        Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool or llm_pool,
            entities=entities,
            subqueries=[question],
        ),
        False,
    )
