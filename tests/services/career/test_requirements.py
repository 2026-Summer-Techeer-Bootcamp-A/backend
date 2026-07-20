import json

from app.services.career.requirements import extract_requirements


class _FakeLLM:
    def __init__(self, payload):
        self._p = payload
        self.last_debug = None
        self.call_count = 0

    def json(self, system, prompt, temperature=0.2, *, max_output_tokens=None):
        return self._p

    def text(self, *a, **k):
        return None


def test_extract_parses_llm_requirements():
    desc = '[{"title":"자격요건","text":"FastAPI로 결제 API를 설계·운영할 분"}]'
    llm = _FakeLLM(
        {
            "items": [
                {
                    "id": "R1",
                    "text": "FastAPI 기반 API 개발",
                    "source_quote": "FastAPI로 결제 API를 설계·운영할 분",
                },
            ]
        }
    )
    reqs, ok = extract_requirements(desc, seed_tags=["FastAPI"], llm=llm)
    assert ok is True
    assert reqs == [
        {
            "id": "R1",
            "text": "FastAPI 기반 API 개발",
            "source_quote": "FastAPI로 결제 API를 설계·운영할 분",
            "kind": "must",
        }
    ]


def test_extract_falls_back_to_tags_when_llm_none():
    llm = _FakeLLM(None)
    reqs, ok = extract_requirements("[]", seed_tags=["FastAPI", "PostgreSQL"], llm=llm)
    assert ok is False  # 태그 폴백은 결과가 비어있지 않아도 LLM 출처가 아니다
    assert [r["text"] for r in reqs] == ["FastAPI", "PostgreSQL"]
    assert reqs[0]["id"] == "R1"
    assert reqs[0]["source_quote"] == ""
    assert reqs[0]["kind"] == "must"  # 태그 폴백은 섹션 근거가 없어 must로 기본 처리한다


def test_extract_falls_back_to_tags_when_description_empty():
    """description이 비어있으면(원문 자체가 없음) LLM을 호출조차 하지 않고 태그
    폴백으로 빠지므로 llm_ok는 False다."""
    llm = _FakeLLM({"items": [{"id": "R1", "text": "안 쓰일 응답"}]})
    reqs, ok = extract_requirements(None, seed_tags=["Docker"], llm=llm)
    assert ok is False
    assert [r["text"] for r in reqs] == ["Docker"]


def test_extract_tags_preferred_section_quote_as_preferred():
    """자격 요건과 우대 사항 두 섹션이 있는 공고에서, source_quote가 우대 사항
    섹션 텍스트 안에 있으면 kind가 preferred로, 자격 요건 섹션 안에 있으면
    must로 갈린다 — LLM이 kind를 직접 답하지 않아도 섹션 근거만으로 정해진다."""
    desc = json.dumps(
        [
            {"title": "자격 요건", "text": "Python 3년 이상 실무 경험"},
            {"title": "우대 사항", "text": "AWS 인프라 운영 경험 우대"},
        ],
        ensure_ascii=False,
    )
    llm = _FakeLLM(
        {
            "items": [
                {"id": "R1", "text": "Python 3년 이상", "source_quote": "Python 3년 이상 실무 경험"},
                {"id": "R2", "text": "AWS 운영 경험", "source_quote": "AWS 인프라 운영 경험 우대"},
            ]
        }
    )
    reqs, ok = extract_requirements(desc, seed_tags=[], llm=llm)
    assert ok is True
    by_id = {r["id"]: r for r in reqs}
    assert by_id["R1"]["kind"] == "must"
    assert by_id["R2"]["kind"] == "preferred"


def test_extract_unmatched_or_unrelated_section_defaults_to_must():
    """source_quote가 어느 섹션에서도 찾아지지 않거나, 찾아졌어도 섹션 라벨이
    자격/우대 어느 쪽도 아니면(예: 주요 업무) must로 기본 처리한다."""
    desc = json.dumps(
        [
            {"title": "주요 업무", "text": "백엔드 API 설계 및 운영"},
        ],
        ensure_ascii=False,
    )
    llm = _FakeLLM(
        {
            "items": [
                {"id": "R1", "text": "API 설계", "source_quote": "백엔드 API 설계 및 운영"},
                {"id": "R2", "text": "지어낸 요구", "source_quote": "본문에 없는 문장"},
            ]
        }
    )
    reqs, ok = extract_requirements(desc, seed_tags=[], llm=llm)
    assert ok is True
    by_id = {r["id"]: r for r in reqs}
    assert by_id["R1"]["kind"] == "must"
    assert by_id["R2"]["kind"] == "must"
