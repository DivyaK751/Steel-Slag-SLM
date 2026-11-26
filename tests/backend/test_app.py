import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import SERVER_ERROR_MESSAGE, create_app
from steel_slag_slm.data.slag_catalogue import OXIDE_LABELS
from steel_slag_slm.pipelines.answer_message import ChatSession
from steel_slag_slm.schemas import ChatEvent


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:  # "with" runs the startup hook
        yield test_client


def _chat(client: TestClient, message: str, session_id: str | None = None) -> list[ChatEvent]:
    """Send one message and parse the streamed events."""
    body = {"session_id": session_id or str(uuid4()), "message": message}
    response = client.post("/chat", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = [block for block in response.text.split("\n\n") if block]
    assert all(block.startswith("data: ") for block in blocks)
    return [ChatEvent.model_validate(json.loads(block.removeprefix("data: "))) for block in blocks]


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_streams_events_in_contract_order(client: TestClient) -> None:
    events = _chat(client, "What is steel slag used for?")

    types = [event.type for event in events]
    first_token = types.index("token")
    assert set(types[:first_token]) == {"status"}
    assert types[-2:] == ["citations", "done"]
    assert set(types[first_token:-2]) == {"token"}
    answer = "".join(event.text for event in events if event.type == "token")
    assert "[P1]" in answer
    assert [citation.label for citation in events[-2].citations] == ["P1", "S1"]


def test_chat_answers_a_blank_message_with_one_error(client: TestClient) -> None:
    events = _chat(client, "   ")

    assert [event.type for event in events] == ["error"]


def test_chat_uses_the_profile_saved_for_the_session(client: TestClient) -> None:
    session_id = str(uuid4())
    profile = {"steel_slag_type": "EAF", "oxides": {"cao_pct": 30, "feo_pct": 25}}

    response = client.put(f"/session/{session_id}/profile", json=profile)
    events = _chat(client, "Can I use it in concrete?", session_id)

    assert response.status_code == 204
    assert "your EAF slag" in "".join(event.text for event in events if event.type == "token")


def test_profile_options_list_every_slag_type_and_oxide(client: TestClient) -> None:
    options = client.get("/profile/options").json()

    assert "EAF" in options["slag_types"]
    assert {"column": "cao_pct", "label": "CaO"} in options["oxides"]
    assert len(options["oxides"]) == len(OXIDE_LABELS)


def test_profile_with_an_unknown_oxide_is_rejected(client: TestClient) -> None:
    profile = {"steel_slag_type": "EAF", "oxides": {"unobtainium_pct": 1}}

    response = client.put(f"/session/{uuid4()}/profile", json=profile)

    assert response.status_code == 422


def test_session_id_that_is_not_a_uuid_is_rejected(client: TestClient) -> None:
    chat = client.post("/chat", json={"session_id": "abc", "message": "hi"})
    profile = client.put("/session/abc/profile", json={"steel_slag_type": "EAF", "oxides": {}})

    assert chat.status_code == 422
    assert profile.status_code == 422


def test_failure_midway_ends_the_stream_with_an_error() -> None:
    def failing_answer(message: str, session: ChatSession) -> Iterator[ChatEvent]:
        yield ChatEvent(type="token", text="Steel ")
        raise RuntimeError("gateway down")

    with TestClient(create_app(answer=failing_answer)) as failing_client:
        events = _chat(failing_client, "hi")

    assert [event.type for event in events] == ["token", "error"]
    assert events[-1].text == SERVER_ERROR_MESSAGE
