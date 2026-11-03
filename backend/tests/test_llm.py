import httpx
import pytest

from app.llm import ChatTurn, LLMError, TogetherClient, build_messages, parse_model_output


def test_parse_json_and_wrapped_json():
    r = parse_model_output('{"reply": "Hello", "topics": ["Returns", "Made up"], "needs_human": false, "note": ""}')
    assert r.reply == "Hello" and r.topics == ["Returns"]
    r = parse_model_output('Sure!\n```json\n{"reply": "Hi", "needs_human": true, "note": "check"}\n```')
    assert r.reply == "Hi" and r.needs_human and r.note == "check"


def test_parse_plain_text_is_flagged():
    r = parse_model_output("Hello there, happy to help.")
    assert r.reply.startswith("Hello") and r.needs_human


def test_parse_garbage_raises():
    with pytest.raises(LLMError):
        parse_model_output('{"topics": []}')


def test_history_roles():
    msgs = build_messages("Aisha", [ChatTurn("customer", "hi"), ChatTurn("agent", "hello"), ChatTurn("customer", "?")])
    assert [m["role"] for m in msgs] == ["system", "system", "user", "assistant", "user"]
    assert "Returns are accepted within 7 days" in msgs[0]["content"]


def _client(handler, monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    c = TogetherClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    return c


def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"reply": "ok"}'}}]})

    assert _client(handler, monkeypatch).generate_draft("A", [ChatTurn("customer", "hi")]).reply == "ok"
    assert calls["n"] == 3


def test_auth_error_not_retried(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    with pytest.raises(LLMError, match="401"):
        _client(handler, monkeypatch).generate_draft("A", [ChatTurn("customer", "hi")])
    assert calls["n"] == 1


def test_falls_back_when_json_mode_unsupported(monkeypatch):
    seen = []

    def handler(request):
        import json
        body = json.loads(request.content)
        seen.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format not supported")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"reply": "ok"}'}}]})

    assert _client(handler, monkeypatch).generate_draft("A", [ChatTurn("customer", "hi")]).reply == "ok"
    assert seen == [True, False]
