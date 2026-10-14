"""Search OpenAlex for papers and download their PDFs within a key's daily budget."""

import logging
import time
from collections.abc import Iterator, Mapping
from typing import Any

import requests

from steel_slag_slm.config import FetchSettings
from steel_slag_slm.exceptions import BudgetExhaustedError, OpenAlexRequestError
from steel_slag_slm.schemas import Paper

logger = logging.getLogger(__name__)

WORKS_URL = "https://api.openalex.org/works"
REMAINING_BUDGET_HEADER = "X-RateLimit-Remaining-USD"
PDF_MAGIC_BYTES = b"%PDF"
RATE_LIMIT_RETRY_DELAY_S = 1.0


def parse_remaining_budget(headers: Mapping[str, str]) -> float | None:
    """Return the remaining daily budget in USD from response headers, if reported."""
    value = headers.get(REMAINING_BUDGET_HEADER)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def rebuild_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Turn OpenAlex's word -> positions index back into plain abstract text."""
    if not inverted_index:
        return ""
    positions = {
        pos: word for word, word_positions in inverted_index.items() for pos in word_positions
    }
    return " ".join(positions[pos] for pos in sorted(positions))


def parse_work(work: dict[str, Any]) -> Paper:
    """Convert one raw OpenAlex work record into a Paper."""
    best_location = work.get("best_oa_location") or {}
    content_urls = work.get("content_urls") or {}
    return Paper(
        openalex_id=work["id"].rsplit("/", 1)[-1],
        title=work.get("title") or "",
        abstract=rebuild_abstract(work.get("abstract_inverted_index")),
        doi=work.get("doi"),
        year=work.get("publication_year"),
        free_pdf_url=best_location.get("pdf_url"),
        paid_pdf_url=content_urls.get("pdf"),
    )


def _is_pdf(response: requests.Response) -> bool:
    return response.ok and response.content[:4] == PDF_MAGIC_BYTES


class OpenAlexClient:
    """OpenAlex access for one API key, tracking how much of its daily budget is left."""

    def __init__(
        self,
        api_key: str,
        contact_email: str,
        settings: FetchSettings,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._session = session or requests.Session()
        # Publishers and OpenAlex both ask for a contact address in the user agent
        self._session.headers["User-Agent"] = f"steel-slag-slm (mailto:{contact_email})"
        self.remaining_budget_usd: float | None = None

    def _billed_get(self, url: str, params: dict[str, Any]) -> requests.Response:
        """GET a billed OpenAlex endpoint, raising once the daily budget is gone.

        Raises:
            BudgetExhaustedError: If OpenAlex keeps answering 429.
            OpenAlexRequestError: If the request times out, drops, or returns a 5xx error.
        """
        params = {**params, "api_key": self._api_key}
        for attempt in range(2):
            try:
                response = self._session.get(
                    url, params=params, timeout=self._settings.request_timeout_s
                )
                if response.status_code >= 500:
                    response.raise_for_status()
            except requests.RequestException as error:
                raise OpenAlexRequestError(f"OpenAlex request failed: {error}") from error
            self.remaining_budget_usd = parse_remaining_budget(response.headers)
            if response.status_code != 429:
                return response
            # 429 is either the per-second cap (retry helps) or the daily budget (it doesn't)
            if attempt == 0:
                time.sleep(RATE_LIMIT_RETRY_DELAY_S)
        raise BudgetExhaustedError("OpenAlex returned 429 twice; daily budget is likely used up")

    def iter_papers(self, query: str) -> Iterator[Paper]:
        """Yield papers matching the query, most cited first, across result pages."""
        cursor: str | None = "*"
        for _ in range(self._settings.max_pages_per_query):
            if not cursor:
                return
            response = self._billed_get(
                WORKS_URL,
                {
                    "search": query,
                    "filter": self._settings.search_filter,
                    "sort": self._settings.sort,
                    "per_page": self._settings.per_page,
                    "cursor": cursor,
                },
            )
            response.raise_for_status()
            page = response.json()
            cursor = page.get("meta", {}).get("next_cursor")
            for work in page.get("results", []):
                yield parse_work(work)

    def download_pdf(self, paper: Paper) -> tuple[bytes, str] | None:
        """Download the paper's PDF, preferring the free copy over the paid one.

        Returns:
            The PDF bytes and their source ("free" or "openalex"), or None if neither worked.

        Raises:
            BudgetExhaustedError: If a paid download is needed but the budget is used up.
            OpenAlexRequestError: If the paid download fails for a temporary reason.
        """
        if paper.free_pdf_url:
            try:
                response = self._session.get(
                    paper.free_pdf_url, timeout=self._settings.request_timeout_s
                )
                if _is_pdf(response):
                    return response.content, "free"
            except requests.RequestException as error:
                logger.debug("Free PDF failed for %s: %s", paper.openalex_id, error)

        if not paper.paid_pdf_url:
            return None
        budget = self.remaining_budget_usd
        if budget is not None and budget < self._settings.min_budget_usd:
            raise BudgetExhaustedError(f"Only ${budget:.3f} left today, not enough for a PDF")
        response = self._billed_get(paper.paid_pdf_url, {})
        return (response.content, "openalex") if _is_pdf(response) else None
