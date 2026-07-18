import app.services.rag.tools.compare_tool as ct


class _FakeLLM:
    last_debug = None
    call_count = 0

    def json(self, *a, **k):
        return None

    def text(self, *a, **k):
        return None


def test_llm_compare_degrades_without_resume_text(monkeypatch):
    monkeypatch.setattr(
        ct,
        "resume_posting_compare",
        lambda **k: {
            "tool": "compare",
            "tool_result": {"kind": "resume_posting", "compare": {"coverage_pct": 40.0}},
            "n": 3,
            "facts": "x",
            "citation": {},
        },
    )
    out = ct.resume_posting_llm_compare(
        session=None, resume_text=None, owned_skill_ids={1}, posting_id=9, llm=_FakeLLM()
    )
    assert out is not None
    assert out["tool_result"]["compare"]["degraded"] is True


def test_llm_compare_degrades_when_requirements_empty(monkeypatch):
    monkeypatch.setattr(
        ct,
        "get_posting_skill_names",
        lambda session, posting_id: ("백엔드 채용", []),
    )
    monkeypatch.setattr(ct, "_get_posting_description", lambda session, posting_id: (None, None))
    monkeypatch.setattr(
        ct,
        "resume_posting_compare",
        lambda **k: {
            "tool": "compare",
            "tool_result": {"kind": "resume_posting", "compare": {"coverage_pct": 0.0}},
            "n": 0,
            "facts": "x",
            "citation": {},
        },
    )
    out = ct.resume_posting_llm_compare(
        session=None,
        resume_text="이력서 원문",
        owned_skill_ids={1},
        posting_id=9,
        llm=_FakeLLM(),
    )
    assert out is not None
    assert out["tool_result"]["compare"]["degraded"] is True


def test_llm_compare_builds_split_diff_payload(monkeypatch):
    monkeypatch.setattr(
        ct,
        "get_posting_skill_names",
        lambda session, posting_id: ("플랫폼 백엔드", ["FastAPI"]),
    )
    monkeypatch.setattr(
        ct,
        "_get_posting_description",
        lambda session, posting_id: ('[{"title":"자격요건","text":"FastAPI로 결제 API 운영"}]', "wanted"),
    )

    class _JudgeLLM:
        last_debug = None
        call_count = 0

        def __init__(self):
            self._n = 0

        def json(self, system, prompt, temperature=0.2, *, max_output_tokens=None):
            self._n += 1
            if self._n == 1:
                return {
                    "items": [
                        {"id": "R1", "text": "FastAPI 개발", "source_quote": "FastAPI로 결제 API 운영"},
                    ]
                }
            return {
                "items": [
                    {
                        "req_id": "R1",
                        "verdict": "met",
                        "resume_quote": "FastAPI 정산 API 운영",
                        "rationale": "일치",
                        "next_step": "",
                    },
                ]
            }

        def text(self, *a, **k):
            return None

    out = ct.resume_posting_llm_compare(
        session=None,
        resume_text="FastAPI 정산 API 운영",
        owned_skill_ids={1},
        posting_id=9,
        llm=_JudgeLLM(),
    )
    assert out is not None
    assert out["tool_result"]["kind"] == "resume_posting_llm"
    compare = out["tool_result"]["compare"]
    assert compare["posting_title"] == "플랫폼 백엔드"
    assert compare["degraded"] is False
    assert compare["counts"] == {"met": 1, "partial": 0, "gap": 0}
    assert compare["score"] == 100.0
    assert compare["requirements"][0]["id"] == "R1"
    assert compare["requirements"][0]["verdict"] == "met"
    assert compare["requirements"][0]["resume_quote"] == "FastAPI 정산 API 운영"
