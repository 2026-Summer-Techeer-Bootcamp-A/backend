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
    reqs = extract_requirements(desc, seed_tags=["FastAPI"], llm=llm)
    assert reqs == [
        {
            "id": "R1",
            "text": "FastAPI 기반 API 개발",
            "source_quote": "FastAPI로 결제 API를 설계·운영할 분",
        }
    ]


def test_extract_falls_back_to_tags_when_llm_none():
    llm = _FakeLLM(None)
    reqs = extract_requirements("[]", seed_tags=["FastAPI", "PostgreSQL"], llm=llm)
    assert [r["text"] for r in reqs] == ["FastAPI", "PostgreSQL"]
    assert reqs[0]["id"] == "R1"
    assert reqs[0]["source_quote"] == ""
