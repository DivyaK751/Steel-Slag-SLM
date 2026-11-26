# 005: A chat app on FastAPI and React, with every chat-time model behind LiteLLM

**Status:** accepted, 2026-11-25

## Context

Question routing (`feature/question-routing`) turns a user's message into cited evidence text,
but nothing turns that evidence into an answer, and there is no chat interface. The final
delivery on 2026-12-11 needs a working chat: question → routing → answer model → a streamed,
cited answer in a browser. The evaluation compares four chat-time models (Gemini Flash, Gemini
Argon 4, Qwen3-8B base and Qwen3-8B fine-tuned), so switching models must not touch code.

The answer model and the UI are built in parallel by different people, so both need one fixed
contract before either is written.

## Decision

- **One model endpoint.** Every chat-time model (the question reader and the answer writer) is
  reached through one LiteLLM endpoint, a separate service, called with the OpenAI SDK from
  `src/steel_slag_slm/llm/gateway.py`. The model name for each job (`reader_model`,
  `answer_model`) and each model's extra parameters come from `configs/base.yaml`; the URL and
  key come from `.env`.
- **Web stack.** A FastAPI backend and a React + Vite + TypeScript frontend.
- **Where code lives.**
  - The chat logic, `answer_message(message, session)`, is in
    `src/steel_slag_slm/pipelines/answer_message.py`, so the CLI, the tests and the API share it.
  - `backend/` (top level) holds only the web layer: routes, in-memory sessions and streaming.
    It imports from `steel_slag_slm` and uses the same `pyproject.toml` and `uv.lock`.
  - `frontend/` (top level) is a separate npm project with its own `package.json`, so it
    cannot live inside the Python package.
- **Event contract.** `POST /chat` streams server-sent events, one `ChatEvent`
  (`steel_slag_slm.schemas`) per event, written as `data: <ChatEvent JSON>\n\n`.
  - Order: any `status` events, then `token` events, then at most one `citations` event, then
    `done`.
  - An `error` event can come at any point and ends the stream in place of `done`. The last
    event is always exactly one `done` or `error`.
  - `status.text` is a step shown to the user ("Searching papers…"); `token.text` is the next
    piece of the answer; `error.text` is a message shown to the user.
  - `citations.citations` holds one `Citation` for each `[S#]`/`[P#]` label the answer uses,
    with the paper, authors, year, DOI and pages a hover card shows.
  - The browser reads the stream with `fetch` and a `ReadableStream`, because `EventSource`
    only sends GET requests.
- **Sessions.** Each chat has a session id. The backend keeps a `ChatSession` (the user's slag
  profile, and from the routing merge on, the previous question's form for follow-ups) in
  memory, keyed by that id. There is no login.

## Consequences

- Comparing models is a config change, and the routing reader moves from the local Ollama model
  to the gateway with no other change, as long as `complete_json` keeps the reader's
  `JsonModel` signature.
- Sessions are lost when the server restarts, and the backend must run as one process. This is
  enough for a demo, but not for many users.
- Until routing merges, `answer_message` is a stub that yields fixed events in the contract's
  order, so the API and UI can be built and tested against it.
- The frontend depends on the event contract, so changing it means amending this record and
  the frontend in the same PR.
