from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.core.config import settings
from app.schemas.resume import ResumeFeedbackResponse


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
) -> ResumeFeedbackResponse:
    try:
        feedback, questions = _generate_with_gemini(skills=skills, position=position)
        return ResumeFeedbackResponse(
            feedback=feedback,
            questions=questions,
            model="primary",
            degraded=False,
        )
    except Exception:
        feedback, questions = _generate_fallback(skills=skills, position=position)
        return ResumeFeedbackResponse(
            feedback=feedback,
            questions=questions,
            model="fallback",
            degraded=True,
        )


def _generate_with_gemini(
    *,
    skills: list[dict[str, Any]],
    position: str,
) -> tuple[list[str], list[str]]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    request_body = {
        "model": settings.gemini_model,
        "system_instruction": (
            "You are a Korean technical resume reviewer. "
            "Return only valid JSON with feedback and questions arrays."
        ),
        "input": _build_prompt(skills=skills, position=position),
        "generation_config": {
            "temperature": 0.4,
        },
    }
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
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


def _build_prompt(*, skills: list[dict[str, Any]], position: str) -> str:
    skill_names = [str(skill.get("canonical", "")).strip() for skill in skills]
    skill_names = [skill for skill in skill_names if skill]
    return json.dumps(
        {
            "task": "확정된 이력서 스킬셋을 기준으로 개선 피드백과 예상 면접 질문을 생성하세요.",
            "position": position,
            "skills": skill_names,
            "requirements": [
                "feedback는 2~4개, questions는 3~5개를 작성하세요.",
                "각 문장은 한국어로 작성하세요.",
                "스킬셋에 없는 경험을 단정하지 말고, 이력서에 드러나면 좋은 보완점으로 표현하세요.",
                "응답은 JSON 객체만 반환하세요.",
            ],
            "schema": {"feedback": ["string"], "questions": ["string"]},
        },
        ensure_ascii=False,
    )


def _extract_text(response_body: dict[str, Any]) -> str:
    output_text = response_body.get("output_text")
    if isinstance(output_text, str):
        return output_text

    texts: list[str] = []
    for step in response_body.get("steps", []):
        for part in step.get("content", []):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    if texts:
        return "\n".join(texts)

    candidates = response_body.get("candidates", [])
    for candidate in candidates:
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
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
