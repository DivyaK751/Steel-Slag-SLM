import io
import json

import pytest

from steel_slag_slm.llm import local_model
from steel_slag_slm.llm.local_model import OllamaJsonModel


def _serve(monkeypatch, content: str) -> list[dict]:
    """Make urlopen answer with this message content; return the request bodies it saw."""
    sent: list[dict] = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr(local_model.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_the_schema_is_sent_as_the_output_format_and_the_answer_parsed(monkeypatch):
    sent = _serve(monkeypatch, '{"message_type": "chat"}')
    model = OllamaJsonModel("http://localhost:11434/", "qwen2.5:3b", 30)

    answer = model.complete_json("rules", "hi", {"type": "object"})

    assert answer == {"message_type": "chat"}
    assert sent[0]["format"] == {"type": "object"}
    assert sent[0]["options"]["temperature"] == 0


def test_thinking_is_sent_only_when_it_is_set(monkeypatch):
    sent = _serve(monkeypatch, "{}")
    OllamaJsonModel("http://x", "qwen2.5:7b", 30).complete_json("s", "u", {})
    OllamaJsonModel("http://x", "qwen3:8b", 30, think=False).complete_json("s", "u", {})

    assert "think" not in sent[0]
    assert sent[1]["think"] is False


def test_an_answer_that_is_not_a_json_object_is_reported(monkeypatch):
    _serve(monkeypatch, "[1, 2]")
    with pytest.raises(ValueError, match="not an object"):
        OllamaJsonModel("http://x", "m", 30).complete_json("s", "u", {})
