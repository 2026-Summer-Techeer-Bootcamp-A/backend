from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.insight import get_skill_share
from app.schemas.resume import ResumeFeedbackResponse

logger = logging.getLogger(__name__)

STRICT_INTERVIEWER_SYSTEM_INSTRUCTION = (
    "당신은 매우 엄격한 한국 IT 기업의 시니어 기술 면접관입니다. "
    "지원자의 스킬셋 깊이, 기술적 트레이드오프 이해도, 장애/실패 상황 대응 경험, "
    "실제 프로덕션 운영 경험을 집요하게 검증합니다. "
    "'써봤다', '사용 경험 있다' 수준의 표면적 답변이나 유행어 나열은 절대 인정하지 않고, "
    "구체적인 수치, 의사결정 근거, 대안 비교, 실패와 복구 경험을 요구하세요. "
    "질문은 지원자가 실제로 보유한 스킬과, 현재 채용 시장에서 요구되지만 "
    "지원자에게 부족한 스킬(시장 수요 격차) 두 가지 모두와 연결되어야 합니다. "
    "응답은 반드시 다음 스키마의 유효한 JSON 객체 하나만 반환하세요. "
    '다른 설명, 마크다운, 코드블록 없이 {"feedback": ["..."], "questions": ["..."]} 형식만 출력하세요.'
)


MARKET_SKILLS_BY_POSITION: dict[str, tuple[str, ...]] = {
    "backend": ("Docker", "Kubernetes", "Redis", "PostgreSQL", "CI/CD", "AWS"),
    "frontend": ("TypeScript", "React", "Next.js", "Testing Library", "Web Vitals"),
    "fullstack": ("TypeScript", "React", "Docker", "PostgreSQL", "CI/CD"),
    "devops": ("Kubernetes", "Terraform", "AWS", "CI/CD", "Prometheus", "Linux"),
    "data": ("SQL", "Python", "Spark", "Airflow", "dbt", "MLflow"),
}
DEFAULT_MARKET_SKILLS = ("Git", "SQL", "Docker", "Cloud", "Testing")


def generate_resume_feedback(
    *,
    skills: list[dict[str, Any]],
    position: str,
    session: Session,
    pool: str | None,
    memo: str | None = None,
    certs: list[dict[str, Any]] | None = None,
) -> ResumeFeedbackResponse:
    try:
        market_skills = _get_market_demand_skills(session=session, position=position, pool=pool)
        feedback, questions = _generate_with_gemini(
            skills=skills,
            position=position,
            market_skills=market_skills,
            memo=memo,
            certs=certs,
        )
        return ResumeFeedbackResponse(
            feedback=feedback,
            questions=questions,
            model="primary",
            degraded=False,
        )
    except Exception as exc:
        logger.warning("gemini feedback failed: %s", exc)
        feedback, questions = _generate_fallback(skills=skills, position=position)
        return ResumeFeedbackResponse(
            feedback=feedback,
            questions=questions,
            model="fallback",
            degraded=True,
        )


def _get_market_demand_skills(
    *,
    session: Session,
    position: str,
    pool: str | None,
) -> list[str]:
    if pool:
        items, _sample_size = get_skill_share(session, pool=pool, position=position, top_k=8)
        names = [str(item["canonical"]) for item in items if item.get("canonical")]
        if names:
            return names

    normalized_position = position.lower()
    return list(MARKET_SKILLS_BY_POSITION.get(normalized_position, DEFAULT_MARKET_SKILLS))


def _generate_with_gemini(
    *,
    skills: list[dict[str, Any]],
    position: str,
    market_skills: list[str],
    memo: str | None = None,
    certs: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    request_body = {
        "systemInstruction": {"parts": [{"text": STRICT_INTERVIEWER_SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": _build_prompt(
                            skills=skills,
                            position=position,
                            market_skills=market_skills,
                            memo=memo,
                            certs=certs,
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.gemini_timeout_seconds,
        ) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RuntimeError("Gemini request failed") from exc

    text = _extract_text(response_body)
    parsed = _parse_json_object(text)
    if parsed is None:
        raise ValueError("Gemini response is not valid JSON")
    feedback = _clean_string_list(parsed.get("feedback"))
    questions = _clean_string_list(parsed.get("questions"))
    if not feedback or not questions:
        raise ValueError("Gemini response is missing feedback or questions")
    return feedback, questions


def _build_prompt(
    *,
    skills: list[dict[str, Any]],
    position: str,
    market_skills: list[str],
    memo: str | None = None,
    certs: list[dict[str, Any]] | None = None,
) -> str:
    skill_names = [str(skill.get("canonical", "")).strip() for skill in skills]
    skill_names = [skill for skill in skill_names if skill]
    cert_names = [
        str(cert.get("name", "")).strip() if isinstance(cert, dict) else str(cert).strip()
        for cert in (certs or [])
    ]
    cert_names = [cert for cert in cert_names if cert]
    payload: dict[str, Any] = {
        "task": "확정된 이력서 스킬셋을 기준으로, 엄격한 면접관 관점의 개선 피드백과 예상 면접 질문을 생성하세요.",
        "position": position,
        "skills": skill_names,
        "현재 채용 시장 수요 스킬": market_skills,
        "requirements": [
            "feedback는 2~4개, questions는 4~5개를 작성하세요.",
            "각 문장은 한국어로 작성하세요.",
            "questions는 지원자의 보유 스킬 중 최소 1개, '현재 채용 시장 수요 스킬' 중 지원자가 갖추지 못한 스킬 중 최소 1개와 연결되어야 합니다.",
            "표면적인 용어 나열이 아니라 트레이드오프, 실패 사례, 프로덕션 운영 경험을 파고드는 질문으로 작성하세요.",
            "스킬셋에 없는 경험을 단정하지 말고, 이력서에 드러나면 좋은 보완점으로 표현하세요.",
            "응답은 JSON 객체만 반환하세요.",
        ],
        "schema": {"feedback": ["string"], "questions": ["string"]},
    }
    if cert_names:
        payload["보유 자격증"] = cert_names
    if memo:
        payload["지원자 메모"] = memo
        payload["requirements"].append(
            "지원자 메모에 적힌 맥락(프로젝트 경험, 목표 등)이 있다면 피드백과 질문에 반영하세요."
        )
    return json.dumps(payload, ensure_ascii=False)


def _extract_text(response_body: dict[str, Any]) -> str:
    candidates = response_body.get("candidates") or []
    if not candidates:
        prompt_feedback = response_body.get("promptFeedback")
        raise RuntimeError(
            f"Gemini response has no candidates (promptFeedback={prompt_feedback!r})"
        )

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    parts = candidate.get("content", {}).get("parts", [])
    texts = [part.get("text") for part in parts if isinstance(part.get("text"), str)]
    if not texts:
        prompt_feedback = response_body.get("promptFeedback")
        raise RuntimeError(
            "Gemini response has no text parts "
            f"(finishReason={finish_reason!r}, promptFeedback={prompt_feedback!r})"
        )
    return "\n".join(texts)


def _try_parse_json_object(candidate: str, *, strict: bool = True) -> dict[str, Any] | None:
    try:
        parsed = json.loads(candidate, strict=strict)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Gemini 응답 텍스트를 JSON 객체로 파싱한다.

    Gemini가 JSON 스키마를 어기는 방식은 크게 두 가지다. 한국어 문장 속에
    "React"처럼 용어를 그대로 인용해 문자열 값 안에 이스케이프하지 않은
    안쪽 따옴표가 섞이거나, 문자열 값 안에 리터럴 개행 같은 제어문자가
    그대로 남는 경우다. 둘 다 프로덕션에서 "Expecting ',' delimiter" 류의
    JSONDecodeError로 관측됐고, 이 오류 하나 때문에 멀쩡한 LLM 답변을 통째로
    버리고 규칙 기반 폴백으로 조용히 강등하는 게 실제 버그였다.

    아래 순서로 단계적으로 복구를 시도한다.
    1) 통째로 json.loads로 파싱(기존 동작, 정상 응답은 항상 여기서 끝난다).
    2) strict=False로 다시 파싱(문자열 안 리터럴 제어문자를 허용).
    3) 중괄호 구간만 잘라내 strict=False로 파싱.
    4) 그래도 실패하면, 문자열 값 중간에 낀 것으로 보이는 안쪽 따옴표를
       이스케이프하는 복구 휴리스틱을 적용하고 마지막으로 한 번 더 파싱.
    이 마지막 복구까지 실패하면 예외를 올리지 않고 None을 돌려준다 — 없는
    데이터를 지어내지 않고 정직하게 실패를 알려서, 호출부가 규칙 기반
    폴백으로 강등할 수 있게 한다.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    parsed = _try_parse_json_object(stripped)
    if parsed is not None:
        return parsed

    parsed = _try_parse_json_object(stripped, strict=False)
    if parsed is not None:
        return parsed

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        return None
    braced = stripped[start : end + 1]

    parsed = _try_parse_json_object(braced, strict=False)
    if parsed is not None:
        return parsed

    repaired = _repair_inner_quotes(braced)
    return _try_parse_json_object(repaired, strict=False)


_STRUCTURAL_BEFORE = frozenset(":,[{")
_STRUCTURAL_AFTER = frozenset(":,]}")


def _repair_inner_quotes(text: str) -> str:
    """문자열 값 중간에 낀 것으로 보이는, 이스케이프되지 않은 큰따옴표를 복구한다.

    JSON 문자열의 여는/닫는 따옴표는 앞뒤 중 한쪽이 `:` `,` `[` `{` `]` `}` 같은
    구조 문자와 맞닿아 있다(공백은 건너뛰고 본다). 반대로 "React"처럼 문장
    중간에 낀 따옴표는 앞뒤 모두 일반 문자와 맞닿아 있다. 이 차이를 이용해
    "양쪽 다 구조 문자가 아닌" 따옴표만 골라 이스케이프한다.

    변수 길이 공백을 사이에 둔 앞/뒤 문맥을 봐야 해서 표준 re 모듈의 고정폭
    lookbehind로는 표현할 수 없다. 그래서 정규식 대신 위치를 직접 훑으며
    양옆의 첫 비공백 문자를 찾는 방식으로 구현한다. 마지막 수단으로만
    호출되는 만큼, 애매하면(양쪽 중 하나라도 구조 문자면) 건드리지 않는
    보수적인 판단을 우선한다.
    """

    def _has_structural_neighbor(neighbors: frozenset[str], index: int, step: int) -> bool:
        i = index
        while 0 <= i < len(text) and text[i].isspace():
            i += step
        return 0 <= i < len(text) and text[i] in neighbors

    result: list[str] = []
    for i, char in enumerate(text):
        if char == '"' and (i == 0 or text[i - 1] != "\\"):
            preceded_by_structural = _has_structural_neighbor(_STRUCTURAL_BEFORE, i - 1, -1)
            followed_by_structural = _has_structural_neighbor(_STRUCTURAL_AFTER, i + 1, 1)
            if not preceded_by_structural and not followed_by_structural:
                result.append('\\"')
                continue
        result.append(char)
    return "".join(result)


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _generate_fallback(
    *,
    skills: list[dict[str, Any]],
    position: str,
) -> tuple[list[str], list[str]]:
    normalized_position = position.lower()
    expected_skills = MARKET_SKILLS_BY_POSITION.get(normalized_position, DEFAULT_MARKET_SKILLS)
    owned_skills = {str(skill.get("canonical", "")).lower() for skill in skills}
    missing_skills = [
        skill
        for skill in expected_skills
        if skill.lower() not in owned_skills
    ][:3]

    if missing_skills:
        feedback = [
            f"{position} 직무 기준으로 {', '.join(missing_skills)} 경험이 스킬셋에 드러나지 않아요. 관련 프로젝트나 문제 해결 사례가 있다면 한 줄로 보강해보세요.",
        ]
    else:
        feedback = [
            f"{position} 직무와 연결되는 핵심 스킬은 확인돼요. 각 기술을 어떤 문제 해결에 사용했는지 성과 중심 문장으로 보강해보세요.",
        ]

    feedback.append(
        "스킬 이름만 나열하기보다 트래픽, 성능, 안정성, 협업 같은 결과 지표와 함께 작성하면 면접 질문으로 이어지기 좋아요."
    )

    representative_skills = [str(skill.get("canonical", "")).strip() for skill in skills[:3]]
    representative_skills = [skill for skill in representative_skills if skill]
    first_skill = representative_skills[0] if representative_skills else "가장 자신 있는 기술"
    questions = [
        f"{first_skill}을 실제 프로젝트에서 선택한 이유와 대안 기술을 비교해서 설명해주세요.",
        f"{position} 직무에서 장애나 병목을 발견했을 때 어떤 순서로 원인을 좁혀갈 건가요?",
        "최근 프로젝트에서 기술적으로 가장 어려웠던 문제와 해결 과정을 구체적으로 설명해주세요.",
    ]
    return feedback, questions
