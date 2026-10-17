"""Read the next result pages of every search and queue the new relevant papers."""

import logging

from steel_slag_slm.config import Settings
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.relevance import is_relevant
from steel_slag_slm.data.search_progress import FIRST_PAGE
from steel_slag_slm.exceptions import OpenAlexRequestError

logger = logging.getLogger(__name__)


def discover_papers(
    settings: Settings,
    client: OpenAlexClient,
    queries: list[str],
    progress: dict[str, int],
    skip_ids: set[str],
    queue: set[str],
) -> set[str]:
    """Read each query's next pages, adding relevant papers not in skip_ids to the queue.

    progress and queue are updated as each search finishes, so the caller can save both even
    if the budget runs out partway through.

    Returns:
        The searches that were read to their end and will start over from page 1.

    Raises:
        BudgetExhaustedError: If the OpenAlex budget runs out.
    """
    pages = settings.fetch.pages_per_discovery
    finished: set[str] = set()
    for query in queries:
        first_page = progress.get(query, FIRST_PAGE)
        try:
            result = client.read_search_pages(query, first_page, pages)
        except OpenAlexRequestError as error:
            logger.warning("Search for %s failed, moving to the next query: %s", query, error)
            continue
        progress[query] = result.next_page
        if result.next_page == FIRST_PAGE:
            finished.add(query)
        new_ids = {
            paper.openalex_id
            for paper in result.papers
            if paper.openalex_id not in skip_ids and is_relevant(paper, settings.relevance)
        } - queue
        queue.update(new_ids)
        logger.info(
            "Searched %s pages %d-%d: %d new papers queued",
            query,
            first_page,
            first_page + pages - 1,
            len(new_ids),
        )
    return finished
