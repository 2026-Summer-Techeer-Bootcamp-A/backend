"""build_roadmap_enrichment — LLM 미가용/실패/스키마 불일치 시에도 항상 유효한 폴백을 낸다."""

from app.schemas.roadmap_enrich import RoadmapEnrichRequest, RoadmapEnrichResponse
from app.services.roadmap_enrich import build_roadmap_enrichment


class NullLLM:
    """항상 None을 반환하는 가짜 LLM."""

    def json(self, system: str, prompt: str, temperature: float = 0.2):
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        return None


class BrokenLLM:
    """예외를 던지는 가짜 LLM(네트워크 오류 등을 시뮬레이션)."""

    def json(self, system: str, prompt: str, temperature: float = 0.2):
        raise RuntimeError("boom")

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        raise RuntimeError("boom")


class GarbageLLM:
    """스키마와 맞지 않는 JSON을 반환하는 가짜 LLM."""

    def json(self, system: str, prompt: str, temperature: float = 0.2):
        return {"unexpected": "shape"}

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        return None


class FakeLLM:
    """유효한 JSON을 반환하는 가짜 LLM."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def json(self, system: str, prompt: str, temperature: float = 0.2):
        self.calls.append((system, prompt))
        return self.payload

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        return None


def _sample_request() -> RoadmapEnrichRequest:
    return RoadmapEnrichRequest(
        goal_company="카카오페이증권",
        goal_title="백엔드 엔지니어",
        owned_skills=["Python", "FastAPI"],
        missing_skills=["Kubernetes", "Kafka"],
        concepts=["MSA"],
        certs=["정보처리기사"],
        career_required=3,
        career_mine=1,
    )


def test_fallback_is_valid_schema_when_llm_returns_none() -> None:
    result = build_roadmap_enrichment(_sample_request(), llm=NullLLM())
    assert isinstance(result, RoadmapEnrichResponse)
    # missing_skills(2) + concepts(1) + certs(1) + career gap(1) = 5 steps
    assert len(result.steps) == 5
    labels = [step.label for step in result.steps]
    assert labels[:4] == ["Kubernetes", "Kafka", "MSA", "정보처리기사"]
    types = [step.type for step in result.steps]
    assert types == ["skill", "skill", "concept", "cert", "career"]
    orders = [step.order for step in result.steps]
    assert orders == [1, 2, 3, 4, 5]
    assert result.quick_win == "Kubernetes"


def test_fallback_used_when_llm_raises() -> None:
    result = build_roadmap_enrichment(_sample_request(), llm=BrokenLLM())
    assert isinstance(result, RoadmapEnrichResponse)
    assert len(result.steps) == 5


def test_fallback_used_when_llm_response_fails_validation() -> None:
    result = build_roadmap_enrichment(_sample_request(), llm=GarbageLLM())
    assert isinstance(result, RoadmapEnrichResponse)
    assert len(result.steps) == 5


def test_fallback_handles_no_gaps() -> None:
    request = RoadmapEnrichRequest(
        goal_company="카카오페이증권",
        goal_title="백엔드 엔지니어",
        owned_skills=["Python"],
        missing_skills=[],
        concepts=[],
        certs=[],
        career_required=None,
        career_mine=None,
    )
    result = build_roadmap_enrichment(request, llm=NullLLM())
    assert isinstance(result, RoadmapEnrichResponse)
    assert result.steps == []
    assert result.headline
    assert result.summary
    assert result.quick_win


def test_llm_response_is_used_when_valid() -> None:
    payload = {
        "headline": "카카오페이증권까지, 3개월 백엔드 심화 로드맵",
        "summary": "Kubernetes와 Kafka부터 순서대로 채워가면 목표에 가까워져요.",
        "quick_win": "Kubernetes",
        "steps": [
            {
                "order": 1,
                "label": "Kubernetes",
                "type": "skill",
                "effort": "2주",
                "priority": "high",
                "reason": "카카오페이증권 백엔드 공고에서 자주 요구돼요.",
                "project": "미니 클러스터를 구성해 배포 파이프라인을 만들어보세요.",
            }
        ],
    }
    fake = FakeLLM(payload)
    result = build_roadmap_enrichment(_sample_request(), llm=fake)
    assert result.headline == payload["headline"]
    assert len(result.steps) == 1
    assert result.steps[0].label == "Kubernetes"
    # 프롬프트에 입력 격차 데이터가 실려야 한다.
    _system, prompt = fake.calls[0]
    assert "Kafka" in prompt and "카카오페이증권" in prompt
