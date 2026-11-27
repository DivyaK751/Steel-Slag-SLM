import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import SERVER_ERROR_MESSAGE, create_app
from steel_slag_slm.config import GatewayModel, LlmGatewaySettings
from steel_slag_slm.data.slag_catalogue import OXIDE_LABELS
from steel_slag_slm.pipelines.answer_message import BLANK_MESSAGE, ChatSession
from steel_slag_slm.pipelines.assistant_setup import ModelChoice
from steel_slag_slm.schemas import ChatEvent, Citation

GATEWAY = LlmGatewaySettings(
    base_url="http://gateway.test/v1",
    api_key="key",
    timeout_s=5,
    reader_model="gemini",
    answer_model="gemini",
    models={
        "gemini": GatewayModel(label="Gemini"),
        "qwen": GatewayModel(label="Qwen", thinking_request={"think": True}),
    },
)
CHOICES: list[ModelChoice] = []  # what each fake_answer call was given


def fake_answer(message: str, session: ChatSession, choice: ModelChoice) -> Iterator[ChatEvent]:
    """Stands in for the real assistant: fixed events in the contract's order."""
    CHOICES.append(choice)
    if not message.strip():
        yield ChatEvent(type="error", text=BLANK_MESSAGE)
        return
    yield ChatEvent(type="status", text="Searching…")
    slag = f"your {session.profile.steel_slag_type} slag" if session.profile else "steel slag"
    for token in (f"About {slag}: ", "it works [P1] ", "as tables show [S1]."):
        yield ChatEvent(type="token", text=token)
    citations = [Citation(label="P1", paper_id="W1"), Citation(label="S1", paper_id="W2")]
    yield ChatEvent(type="citations", citations=citations)
    yield ChatEvent(type="done")


@pytest.fixture
def client() -> Iterator[TestClient]:
    CHOICES.clear()
    app = create_app(answer=fake_answer, gateway=GATEWAY)
    with TestClient(app) as test_client:  # "with" runs startup
        yield test_client


def _chat(
    client: TestClient, message: str, session_id: str | None = None, **models: object
) -> list[ChatEvent]:
    """Send one message, with any model fields, and parse the streamed events."""
    body = {"session_id": session_id or str(uuid4()), "message": message, **models}
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
    def failing_answer(
        message: str, session: ChatSession, choice: ModelChoice
    ) -> Iterator[ChatEvent]:
        yield ChatEvent(type="token", text="Steel ")
        raise RuntimeError("gateway down")

    with TestClient(create_app(answer=failing_answer, gateway=GATEWAY)) as failing_client:
        events = _chat(failing_client, "hi")

    assert [event.type for event in events] == ["token", "error"]
    assert events[-1].text == SERVER_ERROR_MESSAGE


def test_models_lists_every_configured_model_and_the_defaults(client: TestClient) -> None:
    options = client.get("/models").json()

    assert options["models"] == [
        {"id": "gemini", "label": "Gemini", "supports_thinking": False},
        {"id": "qwen", "label": "Qwen", "supports_thinking": True},
    ]
    assert (options["reader_default"], options["answer_default"]) == ("gemini", "gemini")


def test_chat_without_models_uses_the_config_defaults(client: TestClient) -> None:
    _chat(client, "hi")

    assert CHOICES == [ModelChoice("gemini", "gemini", thinking=False)]


def test_chat_passes_the_picked_models_and_thinking(client: TestClient) -> None:
    _chat(client, "hi", reader_model="gemini", answer_model="qwen", thinking=True)

    assert CHOICES == [ModelChoice("gemini", "qwen", thinking=True)]


def test_chat_with_an_unknown_model_is_rejected_before_streaming(client: TestClient) -> None:
    body = {"session_id": str(uuid4()), "message": "hi", "answer_model": "gpt-unknown"}

    response = client.post("/chat", json=body)

    assert response.status_code == 422
    assert "gpt-unknown" in response.json()["detail"]
    assert CHOICES == []
