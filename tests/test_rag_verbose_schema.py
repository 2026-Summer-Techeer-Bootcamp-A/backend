from app.services.rag.schemas import ChatRequest, ToolResult


def test_chat_request_verbose_defaults_false() -> None:
    req = ChatRequest(question="React 채용 추이 어때?")
    assert req.verbose is False


def test_chat_request_accepts_verbose_true() -> None:
    req = ChatRequest(question="React 채용 추이 어때?", verbose=True)
    assert req.verbose is True


def test_tool_result_debug_defaults_none() -> None:
    result = ToolResult(kind="list", label="수요 상위 기술", items=[])
    assert result.debug is None


def test_tool_result_accepts_debug_payload() -> None:
    result = ToolResult(
        kind="list",
        label="수요 상위 기술",
        items=[],
        debug={"sql": "SELECT 1", "params": {"pool": None}},
    )
    assert result.debug == {"sql": "SELECT 1", "params": {"pool": None}}
