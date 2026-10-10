"""OpenAlex search and cursor-paginated retrieval."""

import logging
import time
from typing import Any

import requests

from . import config

LOGGER = logging.getLogger(__name__)
WORKS_ENDPOINT = "https://api.openalex.org/works"


def _rate_limit_delay(response: requests.Response, attempt: int) -> float:
    """Get retry delay, failing fast when the shared daily API budget is spent."""
    try:
        body = response.json()
    except ValueError:
        body = {}

    message = str(body.get("message", "")) if isinstance(body, dict) else ""
    if "insufficient budget" in message.casefold() or "daily budget" in message.casefold():
        raise RuntimeError(
            "OpenAlex reports that the unauthenticated network daily budget is "
            "exhausted. Set OPENALEX_API_KEY to a personal API key, or retry after "
            "the budget reset."
        )

    retry_after = response.headers.get("Retry-After")
    if retry_after is None and isinstance(body, dict):
        retry_after = body.get("retryAfter")

    try:
        delay = float(retry_after) if retry_after is not None else 2**attempt
    except (TypeError, ValueError):
        delay = 2**attempt

    if delay > 60:
        raise RuntimeError(
            f"OpenAlex asked this client to wait {delay:g} seconds. Stopping rather "
            "than holding the retrieval process; add an API key or retry later."
        )
    return delay


def build_search_query(
    settings: Any = config,
    application_terms: tuple[str, ...] | None = None,
) -> str:
    """Build a material search, optionally requiring application context."""
    positives = " OR ".join(
        f'"{term}"' for term in settings.POSITIVE_SEARCH_TERMS
    )
    query = f"({positives})"

    if application_terms:
        context = " OR ".join(f'"{term}"' for term in application_terms)
        query += f" AND ({context})"

    exclusions = " OR ".join(
        f'"{term}"' for term in settings.EXCLUSION_TERMS
    )
    return f"{query} NOT ({exclusions})"


def build_filter_string(settings: Any = config) -> str:
    """Build non-text filters; text exclusions belong in the search query."""
    filters = [f"language:{settings.LANGUAGE_FILTER}"]

    if settings.YEAR_FLOOR is not None:
        filters.append(
            f"from_publication_date:{settings.YEAR_FLOOR}-01-01"
        )

    return ",".join(filters)


def _get_json(
    session: requests.Session,
    params: dict[str, Any],
    settings: Any,
) -> dict[str, Any]:
    """Request one page, retrying rate limits, server errors, and network errors."""
    headers = {"User-Agent": "steel-slag-ingestion/0.1"}
    if settings.OPENALEX_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OPENALEX_API_KEY}"

    if settings.OPENALEX_MAILTO:
        params["mailto"] = settings.OPENALEX_MAILTO

    for attempt in range(settings.REQUEST_MAX_RETRIES):
        try:
            response = session.get(
                WORKS_ENDPOINT,
                params=params,
                headers=headers,
                timeout=settings.REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            if attempt + 1 == settings.REQUEST_MAX_RETRIES:
                raise
            time.sleep(2**attempt)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 == settings.REQUEST_MAX_RETRIES:
                response.raise_for_status()

            delay = (
                _rate_limit_delay(response, attempt)
                if response.status_code == 429
                else 2**attempt
            )

            time.sleep(delay)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError("OpenAlex request retries were exhausted.")


def fetch_all_candidates(
    settings: Any = config,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch matching works with cursor pagination."""
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1.")

    params: dict[str, Any] = {
        "search.title_and_abstract": build_search_query(
            settings,
            application_terms=settings.BINDER_CONTEXT_TERMS,
        ),
        "filter": build_filter_string(settings),
        "select": ",".join(settings.SELECT_FIELDS),
        "per_page": settings.OPENALEX_PER_PAGE,
        "cursor": "*",
    }

    records: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    pages_fetched = 0

    with requests.Session() as session:
        while params["cursor"]:
            cursor = params["cursor"]
            if cursor in seen_cursors:
                raise RuntimeError("OpenAlex repeated a cursor; stopping pagination.")
            seen_cursors.add(cursor)

            data = _get_json(session, params.copy(), settings)
            records.extend(data.get("results", []))
            pages_fetched += 1

            meta = data.get("meta", {})
            next_cursor = meta.get("next_cursor")
            LOGGER.info(
                "Fetched page %d; retrieved %d works so far.",
                pages_fetched,
                len(records),
            )

            if not next_cursor or (
                max_pages is not None and pages_fetched >= max_pages
            ):
                break

            params["cursor"] = next_cursor
            time.sleep(settings.OPENALEX_REQUEST_DELAY)

    LOGGER.info("Retrieval complete: %d works across %d pages.", len(records), pages_fetched)
    return records