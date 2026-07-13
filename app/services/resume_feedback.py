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
) -> ResumeFeedbackResponse:
    try:
        market_skills = _get_market_demand_skills(session=session, position=position, pool=pool)
        feedback, questions = _generate_with_gemini(
            skills=skills,
            position=position,
            market_skills=market_skills,
            memo=memo,
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
) -> str:
    skill_names = [str(skill.get("canonical", "")).strip() for skill in skills]
    skill_names = [skill for skill in skill_names if skill]
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


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(stripped[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("Gemini response is not a JSON object")
    return parsed


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
