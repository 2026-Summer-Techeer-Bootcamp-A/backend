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
                    "resume_quote": "FastAPI 정산 API 40개 엔드포인트 운영",
                    "rationale": "일치",
                },
                {
                    "req_id": "R2",
                    "verdict": "met",
                    "resume_quote": "EKS 클러스터 3년 운영",
                    "rationale": "지어냄",
                },
            ]
        }
    )
    out = judge_requirements(REQS, RESUME, llm)
    r1 = next(j for j in out if j["req_id"] == "R1")
    r2 = next(j for j in out if j["req_id"] == "R2")
    assert r1["verdict"] == "met"
    assert r2["verdict"] == "gap"  # 원문에 없는 인용이라 강등
    assert r2["resume_quote"] == ""


def test_weighted_score_math():
    js = [
        {"req_id": "a", "verdict": "met", "resume_quote": "", "rationale": "", "next_step": ""},
        {"req_id": "b", "verdict": "partial", "resume_quote": "", "rationale": "", "next_step": ""},
        {"req_id": "c", "verdict": "gap", "resume_quote": "", "rationale": "", "next_step": ""},
    ]
    assert weighted_score(js) == 50.0  # (1 + 0.5 + 0) / 3 * 100
    assert weighted_score([]) == 0.0
