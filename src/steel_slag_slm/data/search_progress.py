"""Remember which result page each search reads next, as `<query>\t<page>` lines in Drive."""

SEPARATOR = "\t"
FIRST_PAGE = 1


def parse_progress(text: str) -> dict[str, int]:
    """Turn the progress file's lines into {query: next page}, ignoring malformed lines."""
    progress: dict[str, int] = {}
    for line in text.splitlines():
        query, _, page = line.rpartition(SEPARATOR)
        if query and page.strip().isdigit():
            progress[query] = int(page)
    return progress


def format_progress(progress: dict[str, int]) -> str:
    """Write {query: next page} as one `<query>\t<page>` line per search."""
    return "\n".join(f"{query}{SEPARATOR}{page}" for query, page in sorted(progress.items()))
