import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr

from steel_slag_slm.config import GatewayModel, LlmGatewaySettings
from steel_slag_slm.llm.gateway import GatewayClient, ReplyPiece

QWEN_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}
QWEN_THINKING = {"chat_template_kwargs": {"enable_thinking": True}}
SETTINGS = LlmGatewaySettings(
    base_url="http://gateway.test/v1",
    api_key=SecretStr("key"),
    timeout_s=5,
    reader_model="gemini",
    answer_model="gemini",
    models={
        "gemini": GatewayModel(label="Gemini"),
        "qwen": GatewayModel(label="Qwen", request=QWEN_EXTRA, thinking_request=QWEN_THINKING),
    },
)
SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}}


def _client(
    model: str, handler: Callable[[httpx.Request], httpx.Response], thinking: bool = False
) -> tuple[GatewayClient, list[dict[str, Any]]]:
    """A GatewayClient whose HTTP calls go to handler, plus the request bodies it sent."""
    sent: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    sdk = OpenAI(
        base_url=SETTINGS.base_url,
        api_key="key",
        http_client=httpx.Client(transport=httpx.MockTransport(record)),
        max_retries=0,
    )
    return GatewayClient(SETTINGS, model, sdk, thinking=thinking), sent


def _reply(content: str) -> Callable[[httpx.Request], httpx.Response]:
    body = {
        "id": "1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    return lambda _request: httpx.Response(200, json=body)


def _stream(
    pieces: list[str | None | dict[str, str]],
) -> Callable[[httpx.Request], httpx.Response]:
    """A streamed reply: a str is content, a dict a whole delta, None a chunk with no choices."""

    def chunk(piece: str | None | dict[str, str]) -> dict[str, Any]:
        delta = piece if isinstance(piece, dict) else {"content": piece}
        choices = [] if piece is None else [{"index": 0, "delta": delta}]
        return {"id": "1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": choices}  # fmt: skip

    events = "".join(f"data: {json.dumps(chunk(piece))}\n\n" for piece in pieces)
    body = (events + "data: [DONE]\n\n").encode()
    return lambda _request: httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    )


def test_complete_json_sends_the_schema_model_and_extra_fields() -> None:
    client, sent = _client("qwen", _reply('{"kind": "chat"}'))

    assert client.complete_json("sys", "hi", SCHEMA) == {"kind": "chat"}
    (body,) = sent
    assert body["model"] == "qwen"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["temperature"] == 0
    assert body["chat_template_kwargs"] == QWEN_EXTRA["chat_template_kwargs"]
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_complete_json_sends_no_extra_fields_for_a_model_without_them() -> None:
    client, sent = _client("gemini", _reply('{"kind": "chat"}'))

    client.complete_json("sys", "hi", SCHEMA)

    assert "chat_template_kwargs" not in sent[0]


@pytest.mark.parametrize(
    "content",
    [
        '<think>\nthe user greets me\n</think>\n{"kind": "chat"}',
        '```json\n{"kind": "chat"}\n```',
        '<think></think>\n```\n{"kind": "chat"}\n```',
    ],
)
def test_complete_json_reads_past_a_think_block_and_code_fence(content: str) -> None:
    client, _ = _client("gemini", _reply(content))

    assert client.complete_json("sys", "hi", SCHEMA) == {"kind": "chat"}


@pytest.mark.parametrize("content", ["Sure! Here it is.", "[1, 2]", ""])
def test_complete_json_rejects_an_answer_that_is_not_a_json_object(content: str) -> None:
    client, _ = _client("gemini", _reply(content))

    with pytest.raises(ValueError, match="gemini"):
        client.complete_json("sys", "hi", SCHEMA)


def test_stream_text_yields_each_piece_and_skips_empty_chunks() -> None:
    client, sent = _client("qwen", _stream(["Steel ", "", "slag [P1]", None]))

    assert list(client.stream_text("sys", "hi", temperature=0.2, max_tokens=50)) == [
        ReplyPiece("Steel "),
        ReplyPiece("slag [P1]"),
    ]
    assert sent[0]["stream"] is True
    assert (sent[0]["temperature"], sent[0]["max_tokens"]) == (0.2, 50)
    assert sent[0]["chat_template_kwargs"] == QWEN_EXTRA["chat_template_kwargs"]


def test_stream_text_leaves_out_unset_options() -> None:
    client, sent = _client("gemini", _stream(["ok"]))

    assert list(client.stream_text("sys", "hi")) == [ReplyPiece("ok")]
    assert "temperature" not in sent[0]
    assert "max_tokens" not in sent[0]


def test_gateway_client_rejects_a_model_not_in_config() -> None:
    with pytest.raises(ValueError, match="not in llm_gateway.models"):
        GatewayClient(SETTINGS, "gpt-unknown")


def test_thinking_sends_the_thinking_request_to_a_model_that_supports_it() -> None:
    client, sent = _client("qwen", _reply('{"kind": "chat"}'), thinking=True)

    client.complete_json("sys", "hi", SCHEMA)

    assert sent[0]["chat_template_kwargs"] == QWEN_THINKING["chat_template_kwargs"]


def test_thinking_is_ignored_by_a_model_that_cannot_switch_it() -> None:
    client, sent = _client("gemini", _reply('{"kind": "chat"}'), thinking=True)

    client.complete_json("sys", "hi", SCHEMA)

    assert "chat_template_kwargs" not in sent[0]


def _texts(pieces: list[ReplyPiece]) -> list[tuple[str, bool]]:
    return [(piece.text, piece.is_thinking) for piece in pieces]


def test_stream_text_yields_a_reasoning_field_as_thinking() -> None:
    reply = _stream([{"reasoning_content": "Hmm. "}, {"reasoning": "Slag."}, "Answer"])
    client, _ = _client("qwen", reply)

    assert _texts(list(client.stream_text("sys", "hi"))) == [
        ("Hmm. ", True),
        ("Slag.", True),
        ("Answer", False),
    ]


@pytest.mark.parametrize(
    "pieces",
    [
        ["<think>Hmm.</think>Answer"],
        ["<thi", "nk>Hm", "m.</th", "ink>Answer"],  # tags split across chunks
        ["<", "think>", "Hmm.<", "/think", ">", "Answer"],
    ],
)
def test_stream_text_splits_an_inline_think_block_from_the_answer(pieces: list[str]) -> None:
    client, _ = _client("qwen", _stream(list(pieces)))

    replies = list(client.stream_text("sys", "hi"))

    assert "".join(p.text for p in replies if p.is_thinking) == "Hmm."
    assert "".join(p.text for p in replies if not p.is_thinking) == "Answer"


def test_stream_text_keeps_a_lone_angle_bracket_that_starts_no_tag() -> None:
    client, _ = _client("gemini", _stream(["CaO <", " 5%"]))

    assert "".join(p.text for p in client.stream_text("sys", "hi")) == "CaO < 5%"


def test_stream_text_keeps_text_held_back_at_the_end_of_the_stream() -> None:
    client, _ = _client("gemini", _stream(["ends with <th"]))

    assert _texts(list(client.stream_text("sys", "hi"))) == [("ends with ", False), ("<th", False)]
