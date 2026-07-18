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
