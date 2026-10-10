"""Deterministic data and CSV utilities for the ingestion pipeline."""

import csv
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from . import config


def openalex_work_filename(work_id: str) -> str:
    """Return the legacy-compatible PDF name, such as ``W1234567890.pdf``."""
    identifier = work_id.strip().rstrip("/").rsplit("/", maxsplit=1)[-1]
    if not re.fullmatch(r"W\d+", identifier, flags=re.IGNORECASE):
        raise ValueError(f"Not a valid OpenAlex work ID: {work_id!r}")
    return f"{identifier.upper()}.pdf"


def is_valid_pdf(content: bytes) -> bool:
    """Check the PDF file signature rather than trusting the URL or MIME type."""
    return content.startswith(b"%PDF-")


def download_pdf(
    url: str,
    path: str | Path,
    timeout: int = config.PDF_DOWNLOAD_TIMEOUT,
) -> Path:
    """Download a PDF atomically and reject HTML/login pages masquerading as PDFs."""
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("PDF URL must use HTTP or HTTPS.")

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with requests.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            first_chunk = next(response.iter_content(chunk_size=64 * 1024), b"")
            if not is_valid_pdf(first_chunk):
                content_type = response.headers.get("Content-Type", "unknown")
                raise ValueError(
                    f"Response is not a PDF (Content-Type: {content_type})."
                )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(first_chunk)
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        temporary_file.write(chunk)

        with temporary_path.open("rb") as downloaded_file:
            if not is_valid_pdf(downloaded_file.read(5)):
                raise ValueError("Downloaded file failed PDF signature verification.")

        os.replace(temporary_path, output_path)
        temporary_path = None
        return output_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def reconstruct_abstract(
    inverted_index: Mapping[str, Sequence[int]] | None,
) -> str | None:
    """Rebuild readable text from an OpenAlex abstract inverted index."""
    if not inverted_index:
        return None

    valid_positions: list[tuple[str, int]] = []
    for word, positions in inverted_index.items():
        if not isinstance(word, str) or isinstance(positions, (str, bytes)):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                valid_positions.append((word, position))

    if not valid_positions:
        return None

    words = [""] * (max(position for _, position in valid_positions) + 1)
    for word, position in valid_positions:
        words[position] = word

    abstract = " ".join(word for word in words if word)
    return abstract or None


def _normalize_doi(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None

    doi = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    if doi.lower().startswith("doi:"):
        doi = doi[4:].strip()

    return doi.casefold() or None


def dedup_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the first record for each OpenAlex ID or DOI."""
    unique: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_dois: set[str] = set()

    for record in records:
        item = dict(record)
        raw_id = item.get("id")
        work_id = (
            raw_id.strip().rstrip("/").casefold()
            if isinstance(raw_id, str) and raw_id.strip()
            else None
        )
        doi = _normalize_doi(item.get("doi"))

        is_duplicate = (
            (work_id is not None and work_id in seen_ids)
            or (doi is not None and doi in seen_dois)
        )

        # Track identifiers even on duplicate rows, which may contain a DOI
        # missing from the first copy of that work.
        if work_id is not None:
            seen_ids.add(work_id)
        if doi is not None:
            seen_dois.add(doi)

        if not is_duplicate:
            unique.append(item)

    return unique


def route_no_abstract(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Add readable abstracts, then split records with and without one."""
    with_abstract: list[dict[str, Any]] = []
    without_abstract: list[dict[str, Any]] = []

    for record in records:
        item = dict(record)
        abstract = item.get("abstract")

        if not isinstance(abstract, str) or not abstract.strip():
            abstract = reconstruct_abstract(item.get("abstract_inverted_index"))

        if isinstance(abstract, str) and abstract.strip():
            item["abstract"] = abstract.strip()
            with_abstract.append(item)
        else:
            item["abstract"] = ""
            without_abstract.append(item)

    return with_abstract, without_abstract


def _encode_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def _decode_cell(value: str) -> Any:
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def save_csv(
    records: Iterable[Mapping[str, Any]],
    path: str | Path,
    fieldnames: Sequence[str] | None = None,
) -> None:
    """Write records to UTF-8 CSV, preserving nested lists and objects as JSON."""
    rows = [dict(record) for record in records]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    columns = list(fieldnames or ())
    if not columns:
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)

    if not columns:
        output_path.write_text("", encoding="utf-8")
        return

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {key: _encode_cell(value) for key, value in row.items()}
            for row in rows
        )


def load_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read a UTF-8 CSV and restore JSON-encoded lists and objects."""
    with Path(path).open("r", newline="", encoding="utf-8") as csv_file:
        return [
            {key: _decode_cell(value) for key, value in row.items() if key is not None}
            for row in csv.DictReader(csv_file)
        ]