"""Sample OpenAlex query profiles for a human retrieval-quality audit.

This experiment does not change or write to the production ingestion outputs.
It samples the first, middle, and last accessible result pages per profile.
"""

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from paper_collection_pipeline.data_ingestion import config
from paper_collection_pipeline.data_ingestion.io_utils import reconstruct_abstract
from paper_collection_pipeline.data_ingestion.openalex_client import (
    WORKS_ENDPOINT,
    _rate_limit_delay,
    build_filter_string,
    build_search_query,
)

LOGGER = logging.getLogger(__name__)
EXPERIMENT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = EXPERIMENT_DIR / "outputs"
PER_PAGE = 100
SAMPLE_POSITIONS = (0, 24, 49, 74, 99)
LABELS = {"Yes", "No", "Unclear"}

# Keep experimental query variants here, not in production config.py.
QUERY_PROFILES: dict[str, tuple[str, ...]] = {
    "binder_current": config.BINDER_CONTEXT_TERMS,
    "binder_strict": (
        "cementitious",
        "supplementary cementitious",
        "alkali activated",
        "alkali-activated",
        "cement replacement",
        "hydraulic properties",
        "cement paste",
        "cement binder",
        "hydration",
    ),
    "carbonation": (
        "carbonation",
        "carbonated",
        "CO2 mineralization",
        "CO2 sequestration",
        "carbon dioxide sequestration",
    ),
    "characterization": (
        "characterization",
        "characterisation",
        "mineralogical",
        "mineralogy",
        "chemical composition",
        "XRF",
        "XRD",
        "SEM-EDS",
    ),
}


def _request_page(
    session: requests.Session,
    query: str,
    page: int,
) -> dict[str, Any]:
    """Fetch one result page, respecting OpenAlex throttling and retry guidance."""
    params: dict[str, Any] = {
        "search.title_and_abstract": query,
        "filter": build_filter_string(config),
        "select": ",".join(
            (
                "id",
                "doi",
                "title",
                "publication_year",
                "type",
                "language",
                "abstract_inverted_index",
            )
        ),
        "per_page": PER_PAGE,
        "page": page,
    }
    headers = {"User-Agent": "steel-slag-retrieval-audit/0.1"}
    if config.OPENALEX_API_KEY:
        headers["Authorization"] = f"Bearer {config.OPENALEX_API_KEY}"
    if config.OPENALEX_MAILTO:
        params["mailto"] = config.OPENALEX_MAILTO

    for attempt in range(config.REQUEST_MAX_RETRIES):
        try:
            response = session.get(
                WORKS_ENDPOINT,
                params=params,
                headers=headers,
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            if attempt + 1 == config.REQUEST_MAX_RETRIES:
                raise
            time.sleep(2**attempt)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 == config.REQUEST_MAX_RETRIES:
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

    raise RuntimeError("OpenAlex audit request retries were exhausted.")


def _sample_pages(total_count: int) -> list[int]:
    """Choose top, middle, and late pages within OpenAlex basic-paging limits."""
    last_accessible_page = min(max(1, (total_count + PER_PAGE - 1) // PER_PAGE), 100)
    middle_page = max(1, (last_accessible_page + 1) // 2)
    return sorted({1, middle_page, last_accessible_page})


def collect_audit_sample() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Collect a compact, deduplicated sample and per-profile query counts."""
    works: dict[str, dict[str, Any]] = {}
    profile_summary: dict[str, Any] = {}
    fields = (
        "openalex_id",
        "doi",
        "title",
        "publication_year",
        "publication_type",
        "matched_profiles",
        "sample_positions",
        "abstract",
        "human_label",
        "reviewer_notes",
    )

    with requests.Session() as session:
        for profile_name, application_terms in QUERY_PROFILES.items():
            query = build_search_query(config, application_terms)
            first_page = _request_page(session, query, page=1)
            total_count = int(first_page.get("meta", {}).get("count", 0))
            pages = _sample_pages(total_count)
            page_data = {1: first_page}

            for page in pages:
                if page not in page_data:
                    time.sleep(max(config.OPENALEX_REQUEST_DELAY, 1.0))
                    page_data[page] = _request_page(session, query, page)

            sampled_for_profile = 0
            for page in pages:
                results = page_data[page].get("results", [])
                for index in SAMPLE_POSITIONS:
                    if index >= len(results):
                        continue
                    result = results[index]
                    openalex_id = result.get("id")
                    if not isinstance(openalex_id, str) or not openalex_id:
                        continue

                    sampled_for_profile += 1
                    item = works.setdefault(
                        openalex_id,
                        {
                            "openalex_id": openalex_id,
                            "doi": result.get("doi") or "",
                            "title": result.get("title") or "",
                            "publication_year": result.get("publication_year") or "",
                            "publication_type": result.get("type") or "",
                            "_profiles": set(),
                            "_positions": set(),
                            "abstract": reconstruct_abstract(
                                result.get("abstract_inverted_index")
                            )
                            or "",
                            "human_label": "",
                            "reviewer_notes": "",
                        },
                    )
                    item["_profiles"].add(profile_name)
                    rank = (page - 1) * PER_PAGE + index + 1
                    item["_positions"].add(f"{profile_name}:rank-{rank}")

            profile_summary[profile_name] = {
                "openalex_count": total_count,
                "sampled_positions": sampled_for_profile,
                "sampled_pages": pages,
            }
            LOGGER.info(
                "%s: %s matches; sampled %d positions on pages %s",
                profile_name,
                total_count,
                sampled_for_profile,
                pages,
            )
            time.sleep(max(config.OPENALEX_REQUEST_DELAY, 1.0))

    rows: list[dict[str, str]] = []
    for item in works.values():
        rows.append(
            {
                field: (
                    "; ".join(sorted(item["_profiles"]))
                    if field == "matched_profiles"
                    else " | ".join(sorted(item["_positions"]))
                    if field == "sample_positions"
                    else str(item.get(field, ""))
                )
                for field in fields
            }
        )

    rows.sort(key=lambda row: (row["matched_profiles"], row["openalex_id"]))
    summary = {
        "profiles": profile_summary,
        "unique_sampled_works": len(rows),
        "label_values": sorted(LABELS),
        "sampling_note": (
            "Top, middle, and late rank positions were sampled; these are diagnostic "
            "samples, not probability samples and not unbiased precision/recall estimates."
        ),
    }
    return rows, summary


def write_audit_files(
    rows: list[dict[str, str]],
    summary: dict[str, Any],
) -> tuple[Path, Path]:
    """Write experiment artifacts outside the production outputs directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sample_path = OUTPUT_DIR / "retrieval_audit_sample.csv"
    summary_path = OUTPUT_DIR / "retrieval_audit_summary.json"

    if rows:
        with sample_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        sample_path.write_text("", encoding="utf-8")

    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return sample_path, summary_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    rows, summary = collect_audit_sample()
    sample_path, summary_path = write_audit_files(rows, summary)
    print(f"Sample for review: {sample_path}")
    print(f"Query counts and sample details: {summary_path}")
    print(f"Unique works in sample: {len(rows)}")


if __name__ == "__main__":
    main()