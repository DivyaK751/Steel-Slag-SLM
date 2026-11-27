"""Call any chat-time model through the team's LiteLLM gateway (see docs/adr/005).

The gateway speaks the OpenAI API, so the OpenAI SDK is pointed at its URL. A GatewayClient is
bound to one model, so it can stand in wherever a JsonModel (complete_json) is expected.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI, omit
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from steel_slag_slm.config import LlmGatewaySettings

# A reasoning block some models put before their answer, and a ```json fence around it
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


@dataclass(frozen=True, slots=True)
class ReplyPiece:
    """One streamed piece of a reply: part of the answer, or of the model's reasoning."""

    text: str
    is_thinking: bool = False


class JsonModel(Protocol):
    """Anything that answers a prompt with a JSON object matching a schema."""

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def _strip_reply(text: str) -> str:
    """The reply without a leading reasoning block or a code fence around it."""
    text = _THINK_BLOCK.sub("", text, count=1)
    fenced = _CODE_FENCE.match(text)
    return (fenced.group(1) if fenced else text).strip()


def _partial_tag_length(text: str, tag: str) -> int:
    """How many characters at the end of text could be the start of tag."""
    return next((k for k in range(len(tag) - 1, 0, -1) if text.endswith(tag[:k])), 0)


class _ThinkSplitter:
    """Split streamed text at inline <think>...</think> tags, which may span chunks."""

    def __init__(self) -> None:
        self._is_thinking = False
        self._pending = ""  # the end of the last chunk, which may be half a tag

    def feed(self, text: str) -> Iterator[ReplyPiece]:
        """Yield the pieces of text that are known not to be part of a tag."""
        text, self._pending = self._pending + text, ""
        while text:
            tag = _THINK_CLOSE if self._is_thinking else _THINK_OPEN
            at = text.find(tag)
            if at < 0:
                cut = len(text) - _partial_tag_length(text, tag)
                yield from self._piece(text[:cut])
                self._pending = text[cut:]
                return
            yield from self._piece(text[:at])
            self._is_thinking = not self._is_thinking
            text = text[at + len(tag) :]

    def flush(self) -> Iterator[ReplyPiece]:
        """Yield what is held back once the stream ends: it was not a tag after all."""
        yield from self._piece(self._pending)
        self._pending = ""

    def _piece(self, text: str) -> Iterator[ReplyPiece]:
        if text:
            yield ReplyPiece(text, self._is_thinking)


def connect(settings: LlmGatewaySettings) -> OpenAI:
    """An OpenAI SDK client pointed at the gateway, which several GatewayClients can share."""
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key.get_secret_value(),
        timeout=settings.timeout_s,
    )


class GatewayClient:
    """One model behind the LiteLLM gateway, sent its extra request fields from config.

    With thinking on, a model that supports it is sent its thinking_request instead.
    """

    def __init__(
        self,
        settings: LlmGatewaySettings,
        model: str,
        client: OpenAI | None = None,
        thinking: bool = False,
    ) -> None:
        if model not in settings.models:
            raise ValueError(f"model {model!r} is not in llm_gateway.models in base.yaml")
        entry = settings.models[model]
        self.model = model
        self.label = entry.label
        is_thinking = thinking and entry.thinking_request is not None
        self._extra_body = (entry.thinking_request if is_thinking else entry.request) or None
        self._client = client or connect(settings)

    def _messages(self, system: str, user: str) -> list[ChatCompletionMessageParam]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the model's answer, constrained to the schema, as a parsed JSON object.

        Raises:
            ValueError: If the model answers with something that is not a JSON object.
        """
        response_format: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        }
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            response_format=response_format,
            temperature=0,  # the same message gives the same answer
            extra_body=self._extra_body,
        )
        text = _strip_reply(response.choices[0].message.content or "")
        try:
            answer = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"{self.model} did not answer with JSON") from error
        if not isinstance(answer, dict):
            raise ValueError(f"{self.model} answered with {type(answer).__name__}, not an object")
        return answer

    def stream_text(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[ReplyPiece]:
        """Yield the model's reply piece by piece, as the gateway streams it.

        Reasoning comes as thinking pieces, whether the server sends it in its own field
        (vLLM with a reasoning parser) or inline in a <think> block. temperature and
        max_tokens left as None keep the model's defaults.
        """
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            stream=True,
            # omit leaves an unset option out of the request instead of sending null
            temperature=omit if temperature is None else temperature,
            max_tokens=omit if max_tokens is None else max_tokens,
            extra_body=self._extra_body,
        )
        splitter = _ThinkSplitter()
        for chunk in stream:
            if not chunk.choices:  # the last chunk may carry only usage
                continue
            delta = chunk.choices[0].delta
            # Not in the SDK's types: vLLM and LiteLLM add it, under either name
            reasoning = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if reasoning:
                yield ReplyPiece(reasoning, is_thinking=True)
            if delta.content:
                yield from splitter.feed(delta.content)
        yield from splitter.flush()
