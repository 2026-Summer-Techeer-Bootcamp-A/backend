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
