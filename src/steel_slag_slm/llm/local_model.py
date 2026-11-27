"""Call a small open model served locally (Ollama) and get JSON that follows a given schema."""

import json
import urllib.request
from typing import Any, Protocol

CONTEXT_TOKENS = 8192  # room for the instruction, column list, value lists and a follow-up


class JsonModel(Protocol):
    """Anything that answers a prompt with a JSON object matching a schema."""

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class OllamaJsonModel:
    """A model served by Ollama, with its output forced to the schema (structured outputs)."""

    def __init__(
        self, base_url: str, model: str, timeout_s: float, think: bool | None = None
    ) -> None:
        """Set think=False for models that reason before answering (Qwen3); None sends nothing."""
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._timeout_s = timeout_s
        self._think = think

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the model's answer as a parsed JSON object.

        Raises:
            ValueError: If the server answers with something that is not a JSON object.
            OSError: If the server cannot be reached.
        """
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            # temperature 0: the same question gives the same form. num_ctx: Ollama's default
            # window (4096 tokens) silently cuts longer prompts, and the column list is long
            "options": {"temperature": 0, "num_ctx": CONTEXT_TOKENS},
        }
        if self._think is not None:
            body["think"] = self._think
        request = urllib.request.Request(
            self._url, json.dumps(body).encode(), {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
            reply = json.loads(response.read())
        try:
            answer = json.loads(reply["message"]["content"])
        except (KeyError, json.JSONDecodeError) as error:
            raise ValueError(f"{self._model} did not answer with JSON") from error
        if not isinstance(answer, dict):
            raise ValueError(f"{self._model} answered with {type(answer).__name__}, not an object")
        return answer
