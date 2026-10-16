import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from steel_slag_slm.agents.runner import gather_bounded, run_structured
from steel_slag_slm.exceptions import AgentOutputError


class Answer(BaseModel):
    value: int


class FakeSessions:
    def __init__(self) -> None:
        self.live: set[str] = set()

    async def create_session(self, **_kwargs: Any) -> SimpleNamespace:
        self.live.add("s1")
        return SimpleNamespace(id="s1")

    async def delete_session(self, *, session_id: str, **_kwargs: Any) -> None:
        self.live.discard(session_id)


class FakeRunner:
    """Replays one final event with the given text, like an ADK runner would."""

    def __init__(self, text: str) -> None:
        self.session_service = FakeSessions()
        self._text = text

    async def run_async(self, **_kwargs: Any) -> Any:
        part = SimpleNamespace(text=self._text)
        yield SimpleNamespace(is_final_response=lambda: True, content=SimpleNamespace(parts=[part]))


def test_run_structured_parses_answer_and_deletes_session() -> None:
    runner = FakeRunner('{"value": 3}')
    answer = asyncio.run(run_structured(runner, "hi", Answer))  # type: ignore[arg-type]
    assert answer.value == 3
    assert not runner.session_service.live


@pytest.mark.parametrize("text", ["", '{"value": "three"}'])
def test_run_structured_rejects_missing_or_malformed_answer(text: str) -> None:
    with pytest.raises(AgentOutputError):
        asyncio.run(run_structured(FakeRunner(text), "hi", Answer))  # type: ignore[arg-type]


def test_gather_bounded_keeps_order_caps_concurrency_and_returns_errors() -> None:
    in_flight = 0
    peak = 0

    async def work(item: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        if item == 2:
            raise ValueError("bad item")
        return item * 10

    results = asyncio.run(gather_bounded([1, 2, 3, 4], work, max_concurrency=2))
    assert results[0] == 10 and results[2:] == [30, 40]
    assert isinstance(results[1], ValueError)
    assert peak == 2
