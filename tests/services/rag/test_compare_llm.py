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
                        "quote": "FastAPI 정산 API 운영",
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
    assert compare["requirements"][0]["quote"] == "FastAPI 정산 API 운영"


# ---- posting_posting_llm_compare: 공고 대 공고 LLM 판정(judge.py target_label 재사용) ----


def test_posting_llm_compare_returns_none_when_posting_missing(monkeypatch):
    from fastapi import HTTPException

    def _raise(session, posting_id):
        raise HTTPException(status_code=404, detail="posting not found")

    monkeypatch.setattr(ct, "get_posting_skill_names", _raise)
    out = ct.posting_posting_llm_compare(
        session=None, posting_id_a=1, posting_id_b=999999, llm=_FakeLLM()
    )
    assert out is None


def test_posting_llm_compare_degrades_when_requirements_empty(monkeypatch):
    monkeypatch.setattr(
        ct,
        "get_posting_skill_names",
        lambda session, posting_id: ("공고 A" if posting_id == 1 else "공고 B", []),
    )
    monkeypatch.setattr(ct, "_get_posting_description", lambda session, posting_id: (None, None))
    monkeypatch.setattr(
        ct,
        "posting_posting_compare",
        lambda session, posting_id_a, posting_id_b: {
            "tool": "compare",
            "tool_result": {"kind": "posting_posting", "compare": {"shared": []}},
            "n": 0,
            "facts": "x",
            "citation": {},
        },
    )
    out = ct.posting_posting_llm_compare(
        session=None, posting_id_a=1, posting_id_b=2, llm=_FakeLLM()
    )
    assert out is not None
    assert out["tool_result"]["kind"] == "posting_posting"
    assert out["tool_result"]["compare"]["degraded"] is True


def test_posting_llm_compare_degrades_when_judge_llm_fails(monkeypatch):
    """요구사항 추출까지는 성공했지만(req_llm_ok=True) 판정 호출이 items를 하나도
    돌려주지 못하면(judge_llm_ok=False) 태그 비교로 강등해야 한다."""
    monkeypatch.setattr(
        ct,
        "get_posting_skill_names",
        lambda session, posting_id: ("공고 A" if posting_id == 1 else "공고 B", ["FastAPI"]),
    )
    monkeypatch.setattr(
        ct,
        "_get_posting_description",
        lambda session, posting_id: (
            '[{"title":"자격요건","text":"FastAPI로 결제 API 운영"}]',
            "wanted",
        ),
    )
    monkeypatch.setattr(
        ct,
        "posting_posting_compare",
        lambda session, posting_id_a, posting_id_b: {
            "tool": "compare",
            "tool_result": {"kind": "posting_posting", "compare": {"shared": []}},
            "n": 0,
            "facts": "x",
            "citation": {},
        },
    )

    class _ExtractOnlyLLM:
        last_debug = None
        call_count = 0

        def json(self, system, prompt, temperature=0.2, *, max_output_tokens=None):
            if "공고 본문" in prompt:
                return {
                    "items": [
                        {"id": "R1", "text": "FastAPI 개발", "source_quote": "FastAPI로 결제 API 운영"},
                    ]
                }
            return None  # 판정 호출은 실패

        def text(self, *a, **k):
            return None

    out = ct.posting_posting_llm_compare(
        session=None, posting_id_a=1, posting_id_b=2, llm=_ExtractOnlyLLM()
    )
    assert out is not None
    assert out["tool_result"]["kind"] == "posting_posting"
    assert out["tool_result"]["compare"]["degraded"] is True


def test_posting_llm_compare_builds_llm_payload(monkeypatch):
    descriptions = {
        1: '[{"title":"자격요건","text":"FastAPI로 결제 API 운영"}]',
        2: '[{"title":"경력사항","text":"FastAPI 정산 API 운영"}]',
    }
    monkeypatch.setattr(
        ct,
        "get_posting_skill_names",
        lambda session, posting_id: (
            "플랫폼 백엔드" if posting_id == 1 else "결제 백엔드",
            ["FastAPI"],
        ),
    )
    monkeypatch.setattr(
        ct,
        "_get_posting_description",
        lambda session, posting_id: (descriptions[posting_id], "wanted"),
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
            # 판정 호출은 target_label="비교 대상 공고"를 프롬프트에 실어 보낸다.
            assert "비교 대상 공고 원문:" in prompt
            return {
                "items": [
                    {
                        "req_id": "R1",
                        "verdict": "met",
                        "quote": "FastAPI 정산 API 운영",
                        "rationale": "일치",
                        "next_step": "",
                    },
                ]
            }

        def text(self, *a, **k):
            return None

    out = ct.posting_posting_llm_compare(
        session=None, posting_id_a=1, posting_id_b=2, llm=_JudgeLLM()
    )
    assert out is not None
    assert out["tool_result"]["kind"] == "posting_posting_llm"
    assert out["citation"]["ref"] == "플랫폼 백엔드 vs 결제 백엔드"
    compare = out["tool_result"]["compare"]
    assert compare["base_role"] == "공고"
    assert compare["base_title"] == "플랫폼 백엔드"
    assert compare["target_role"] == "비교 공고"
    assert compare["target_title"] == "결제 백엔드"
    assert compare["degraded"] is False
    assert compare["counts"] == {"met": 1, "partial": 0, "gap": 0}
    assert compare["score"] == 100.0
    assert compare["requirements"][0]["id"] == "R1"
    assert compare["requirements"][0]["verdict"] == "met"
    assert compare["requirements"][0]["quote"] == "FastAPI 정산 API 운영"
