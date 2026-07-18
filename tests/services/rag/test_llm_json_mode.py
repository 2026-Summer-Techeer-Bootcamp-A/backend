from unittest.mock import patch

from app.services.rag import llm as llm_mod


def _fake_urlopen_factory(captured: dict):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            return json.dumps(
                {
                    "candidates": [{"content": {"parts": [{"text": '{"items": [1, 2]}'}]}}],
                    "usageMetadata": {},
                }
            ).encode("utf-8")

    def _fake(req, timeout=None):
        import json

        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    return _fake


def test_json_sets_response_mime_type_and_token_override():
    captured: dict = {}
    with (
        patch.object(llm_mod.settings, "gemini_api_key", "k"),
        patch.object(llm_mod.urllib.request, "urlopen", _fake_urlopen_factory(captured)),
    ):
        client = llm_mod.GeminiClient()
        out = client.json("sys", "prompt", temperature=0.0, max_output_tokens=4096)
    assert out == {"items": [1, 2]}
    gen = captured["body"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["maxOutputTokens"] == 4096


def test_json_omits_thinking_level_for_gemini_2_5_model():
    """gemini-2.5 계열에는 thinkingLevel을 보내면 안 된다(HTTP 400의 원인이었다).

    대신 thinkingBudget(정수)을 보내야 한다.
    """
    captured: dict = {}
    with (
        patch.object(llm_mod.settings, "gemini_api_key", "k"),
        patch.object(llm_mod.settings, "gemini_model", "gemini-2.5-flash"),
        patch.object(llm_mod.settings, "gemini_thinking_level", "minimal"),
        patch.object(llm_mod.urllib.request, "urlopen", _fake_urlopen_factory(captured)),
    ):
        client = llm_mod.GeminiClient()
        client.json("sys", "prompt", temperature=0.0)
    gen = captured["body"]["generationConfig"]
    assert "thinkingConfig" in gen
    assert "thinkingLevel" not in gen["thinkingConfig"]
    assert gen["thinkingConfig"] == {"thinkingBudget": 0}


def test_json_sends_thinking_level_for_gemini_3_model():
    captured: dict = {}
    with (
        patch.object(llm_mod.settings, "gemini_api_key", "k"),
        patch.object(llm_mod.settings, "gemini_model", "gemini-3.5-flash"),
        patch.object(llm_mod.settings, "gemini_thinking_level", "minimal"),
        patch.object(llm_mod.urllib.request, "urlopen", _fake_urlopen_factory(captured)),
    ):
        client = llm_mod.GeminiClient()
        client.json("sys", "prompt", temperature=0.0)
    gen = captured["body"]["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "minimal"}


class TestParseJsonObject:
    """_parse_json_object가 최상위 배열 응답도 items로 감싸 살려내는지 확인한다.

    extract_requirements/judge_requirements는 프롬프트에서 "items 배열로 답한다"고
    요청하는데, 모델이 이를 그대로 따라 최상위가 {"items":[...]}가 아니라 바로
    [...]인 응답을 종종 내놓았다 — 기존 중괄호 스캔은 이런 응답을 mangled 조각으로
    잘라내 None을 반환했다(6/6 재현, posting 129 기준).
    """

    def test_bare_array_is_wrapped_in_items(self):
        assert llm_mod._parse_json_object('[{"id":"R1"}]') == {"items": [{"id": "R1"}]}

    def test_plain_object_still_parses(self):
        assert llm_mod._parse_json_object('{"items":[1,2]}') == {"items": [1, 2]}

    def test_fenced_array_is_wrapped_in_items(self):
        text = '```json\n[{"id":"R1"}]\n```'
        assert llm_mod._parse_json_object(text) == {"items": [{"id": "R1"}]}


class TestThinkingConfig:
    def test_gemini_2_5_minimal_uses_zero_budget(self):
        assert llm_mod._thinking_config("gemini-2.5-flash", "minimal") == {
            "thinkingBudget": 0
        }

    def test_gemini_2_5_non_minimal_uses_dynamic_budget(self):
        assert llm_mod._thinking_config("gemini-2.5-flash", "high") == {
            "thinkingBudget": -1
        }

    def test_gemini_3_uses_thinking_level(self):
        assert llm_mod._thinking_config("gemini-3.5-flash", "minimal") == {
            "thinkingLevel": "minimal"
        }

    def test_unknown_model_omits_thinking_config(self):
        assert llm_mod._thinking_config("some-unknown-model", "minimal") is None
