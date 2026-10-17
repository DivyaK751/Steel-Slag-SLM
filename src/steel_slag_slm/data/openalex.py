"""Search OpenAlex for papers and download their PDFs within a key's daily budget."""

import logging
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
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
# OpenAlex accepts at most 100 values in one OR filter
MAX_IDS_PER_LOOKUP = 100
# Paging by page number (rather than cursor) stops at this many results per search
MAX_RESULTS_BY_PAGE_NUMBER = 10_000


@dataclass(frozen=True, slots=True)
class SearchPages:
    """Papers from a run of consecutive result pages, and where the next run should start."""

    papers: list[Paper]
    next_page: int  # 1 once the search has been read to its end


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
        authors=tuple(
            name
            for authorship in work.get("authorships") or []
            if (name := (authorship.get("author") or {}).get("display_name"))
        ),
        keywords=tuple(
            name for keyword in work.get("keywords") or [] if (name := keyword.get("display_name"))
        ),
        language=work.get("language"),
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
            # UnicodeError: requests can't follow a redirect to a non-UTF-8 URL
            except (requests.RequestException, UnicodeError) as error:
                raise OpenAlexRequestError(f"OpenAlex request failed: {error}") from error
            self.remaining_budget_usd = parse_remaining_budget(response.headers)
            if response.status_code != 429:
                return response
            # 429 is either the per-second cap (retry helps) or the daily budget (it doesn't)
            if attempt == 0:
                time.sleep(RATE_LIMIT_RETRY_DELAY_S)
        raise BudgetExhaustedError("OpenAlex returned 429 twice; daily budget is likely used up")

    def read_search_pages(self, query: str, first_page: int, page_count: int) -> SearchPages:
        """Read page_count result pages of a search, starting at first_page.

        Page numbers, unlike cursors, still work the next day, so a search can resume where
        the last run stopped. Reaching the last page (or OpenAlex's 10,000-result limit for
        page numbers) sets next_page back to 1 so the search starts over.
        """
        per_page = self._settings.per_page
        last_page = MAX_RESULTS_BY_PAGE_NUMBER // per_page
        papers: list[Paper] = []
        for page in range(first_page, first_page + page_count):
            response = self._billed_get(
                WORKS_URL,
                {
                    "search": query,
                    "filter": self._settings.search_filter,
                    "sort": self._settings.sort,
                    "per_page": per_page,
                    "page": page,
                },
            )
            response.raise_for_status()
            body = response.json()
            results = body.get("results", [])
            papers.extend(parse_work(work) for work in results)
            total_pages = -(-body.get("meta", {}).get("count", 0) // per_page)  # ceiling division
            if not results or page >= min(total_pages, last_page):
                return SearchPages(papers, next_page=1)
        return SearchPages(papers, next_page=first_page + page_count)

    def iter_papers_by_id(self, openalex_ids: list[str]) -> Iterator[Paper]:
        """Yield the papers with these OpenAlex IDs, looking up many per request.

        IDs that OpenAlex doesn't know are skipped, so fewer papers than IDs may come back.
        """
        for start in range(0, len(openalex_ids), MAX_IDS_PER_LOOKUP):
            batch = openalex_ids[start : start + MAX_IDS_PER_LOOKUP]
            response = self._billed_get(
                WORKS_URL, {"filter": f"openalex:{'|'.join(batch)}", "per_page": len(batch)}
            )
            response.raise_for_status()
            for work in response.json().get("results", []):
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
            # Publisher links can redirect to non-UTF-8 URLs, which requests can't follow
            except (requests.RequestException, UnicodeError) as error:
                logger.debug("Free PDF failed for %s: %s", paper.openalex_id, error)

        if not paper.paid_pdf_url:
            return None
        budget = self.remaining_budget_usd
        if budget is not None and budget < self._settings.min_budget_usd:
            raise BudgetExhaustedError(f"Only ${budget:.3f} left today, not enough for a PDF")
        response = self._billed_get(paper.paid_pdf_url, {})
        return (response.content, "openalex") if _is_pdf(response) else None
