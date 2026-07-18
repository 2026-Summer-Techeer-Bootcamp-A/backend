from app.services.career.judge import judge_requirements, weighted_score


class _FakeLLM:
    def __init__(self, payload):
        self._p = payload
        self.last_debug = None
        self.call_count = 0

    def json(self, system, prompt, temperature=0.2, *, max_output_tokens=None):
        return self._p

    def text(self, *a, **k):
        return None


REQS = [
    {"id": "R1", "text": "FastAPI 개발", "source_quote": "FastAPI"},
    {"id": "R2", "text": "K8s 운영", "source_quote": "EKS"},
]
RESUME = "FastAPI 정산 API 40개 엔드포인트 운영, p95 120ms."


def test_guard_downgrades_hallucinated_quote():
    llm = _FakeLLM(
        {
            "items": [
                {
                    "req_id": "R1",
                    "verdict": "met",
                    "quote": "FastAPI 정산 API 40개 엔드포인트 운영",
                    "rationale": "일치",
                },
                {
                    "req_id": "R2",
                    "verdict": "met",
                    "quote": "EKS 클러스터 3년 운영",
                    "rationale": "지어냄",
                },
            ]
        }
    )
    out, ok = judge_requirements(REQS, RESUME, llm)
    assert ok is True  # 모델이 R1/R2 둘 다 실제로 판정했다(가드로 R2가 gap 강등돼도 무관)
    r1 = next(j for j in out if j["req_id"] == "R1")
    r2 = next(j for j in out if j["req_id"] == "R2")
    assert r1["verdict"] == "met"
    assert r2["verdict"] == "gap"  # 원문에 없는 인용이라 강등
    assert r2["quote"] == ""


def test_judge_llm_ok_false_when_llm_returns_nothing_usable():
    """LLM이 죽어서(None) items를 하나도 못 만들면, 요구 수만큼 기본 gap으로 채워져
    반환 리스트 자체는 비어있지 않지만 llm_ok는 False여야 한다 — compare_tool이 이걸로
    "판정 성공"과 "전부 기본 gap 채움"을 구분해 정직하게 강등한다."""
    llm = _FakeLLM(None)
    out, ok = judge_requirements(REQS, RESUME, llm)
    assert ok is False
    assert [j["verdict"] for j in out] == ["gap", "gap"]


def test_judge_requirements_empty_returns_false():
    out, ok = judge_requirements([], RESUME, _FakeLLM(None))
    assert out == []
    assert ok is False


def test_weighted_score_math():
    js = [
        {"req_id": "a", "verdict": "met", "quote": "", "rationale": "", "next_step": ""},
        {"req_id": "b", "verdict": "partial", "quote": "", "rationale": "", "next_step": ""},
        {"req_id": "c", "verdict": "gap", "quote": "", "rationale": "", "next_step": ""},
    ]
    assert weighted_score(js) == 50.0  # (1 + 0.5 + 0) / 3 * 100
    assert weighted_score([]) == 0.0
