"""Call any chat-time model through the team's LiteLLM gateway (see docs/adr/005).

The gateway speaks the OpenAI API, so the OpenAI SDK is pointed at its URL. A GatewayClient is
bound to one model, so it can stand in wherever a JsonModel (complete_json) is expected.
"""

import json
import re
from collections.abc import Iterator
from typing import Any, Protocol

from openai import OpenAI, omit
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from steel_slag_slm.config import LlmGatewaySettings

# A reasoning block some models put before their answer, and a ```json fence around it
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class JsonModel(Protocol):
    """Anything that answers a prompt with a JSON object matching a schema."""

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def _strip_reply(text: str) -> str:
    """The reply without a leading reasoning block or a code fence around it."""
    text = _THINK_BLOCK.sub("", text, count=1)
    fenced = _CODE_FENCE.match(text)
    return (fenced.group(1) if fenced else text).strip()


class GatewayClient:
    """One model behind the LiteLLM gateway, sent its extra request fields from config."""

    def __init__(
        self, settings: LlmGatewaySettings, model: str, client: OpenAI | None = None
    ) -> None:
        if model not in settings.models:
            raise ValueError(f"model {model!r} is not in llm_gateway.models in base.yaml")
        self.model = model
        self._extra_body = settings.models[model] or None
        self._client = client or OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key.get_secret_value(),
            timeout=settings.timeout_s,
        )

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
    ) -> Iterator[str]:
        """Yield the model's answer piece by piece, as the gateway streams it.

        temperature and max_tokens left as None keep the model's defaults.
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
        for chunk in stream:
            # The last chunk may carry only usage, with no choices
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
