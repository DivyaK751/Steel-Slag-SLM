from unittest.mock import MagicMock

import pytest
import requests

from steel_slag_slm.config import FetchSettings
from steel_slag_slm.data.openalex import (
    OpenAlexClient,
    parse_remaining_budget,
    parse_work,
    rebuild_abstract,
)
from steel_slag_slm.exceptions import BudgetExhaustedError, OpenAlexRequestError
from steel_slag_slm.schemas import Paper

SETTINGS = FetchSettings(
    queries=["steel slag"],
    search_filter="is_oa:true",
    sort="cited_by_count:desc",
    per_page=200,
    max_pages_per_query=1,
    request_timeout_s=1,
    min_budget_usd=0.011,
)
PAPER = Paper("W1", "t", "", None, None, "https://free.example/p.pdf", "https://paid.example/p")


def _response(
    status: int = 200, content: bytes = b"%PDF-1.7", headers: dict[str, str] | None = None
) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.ok = status < 400
    response.content = content
    response.headers = headers or {}
    return response


def _client(*responses: MagicMock | Exception) -> OpenAlexClient:
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = list(responses)
    return OpenAlexClient("key", "a@b.edu", SETTINGS, session=session)


def test_rebuild_abstract_orders_words_by_position() -> None:
    assert rebuild_abstract({"slag": [1], "Steel": [0], "binders": [2]}) == "Steel slag binders"
    assert rebuild_abstract(None) == ""


def test_parse_work_extracts_short_id_and_pdf_urls() -> None:
    paper = parse_work(
        {
            "id": "https://openalex.org/W2068066265",
            "title": "An Overview of Utilization of Steel Slag",
            "publication_year": 2012,
            "best_oa_location": {"pdf_url": "https://free.example/p.pdf"},
            "content_urls": {"pdf": "https://content.openalex.org/works/W2068066265.pdf"},
        }
    )
    assert paper.openalex_id == "W2068066265"
    assert paper.free_pdf_url == "https://free.example/p.pdf"
    assert paper.paid_pdf_url is not None
    assert paper.paid_pdf_url.endswith("W2068066265.pdf")


def test_parse_remaining_budget_ignores_missing_or_bad_values() -> None:
    assert parse_remaining_budget({"X-RateLimit-Remaining-USD": "0.42"}) == 0.42
    assert parse_remaining_budget({}) is None
    assert parse_remaining_budget({"X-RateLimit-Remaining-USD": "n/a"}) is None


def test_download_pdf_prefers_free_copy() -> None:
    client = _client(_response())
    assert client.download_pdf(PAPER) == (b"%PDF-1.7", "free")


def test_download_pdf_falls_back_to_paid_copy_when_free_is_not_a_pdf() -> None:
    client = _client(_response(content=b"<html>"), _response(content=b"%PDF-paid"))
    assert client.download_pdf(PAPER) == (b"%PDF-paid", "openalex")


def test_download_pdf_raises_when_budget_below_minimum() -> None:
    client = _client(_response(content=b"<html>"))
    client.remaining_budget_usd = 0.005
    with pytest.raises(BudgetExhaustedError):
        client.download_pdf(PAPER)


def test_download_pdf_raises_after_repeated_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("steel_slag_slm.data.openalex.time.sleep", lambda _: None)
    client = _client(_response(content=b"<html>"), _response(429), _response(429))
    with pytest.raises(BudgetExhaustedError):
        client.download_pdf(PAPER)


def test_download_pdf_raises_request_error_when_paid_download_times_out() -> None:
    client = _client(_response(content=b"<html>"), requests.ReadTimeout("read timed out"))
    with pytest.raises(OpenAlexRequestError):
        client.download_pdf(PAPER)


def test_download_pdf_raises_request_error_on_server_error() -> None:
    server_error = _response(status=503, content=b"")
    server_error.raise_for_status.side_effect = requests.HTTPError("503")
    client = _client(_response(content=b"<html>"), server_error)
    with pytest.raises(OpenAlexRequestError):
        client.download_pdf(PAPER)
