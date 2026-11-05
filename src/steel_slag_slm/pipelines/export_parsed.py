"""Materialize indexed Drive parses locally for review and optional figure reading."""

import logging
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf

from steel_slag_slm.config import PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index
from steel_slag_slm.data.parsed_manifest import ParsedManifest
from steel_slag_slm.exceptions import PapersNotFoundError

logger = logging.getLogger(__name__)


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_export_parsed(
    settings: PdfParsingSettings,
    service: Any,
    parsed_folder_id: str,
    output_dir: Path,
    paper_ids: list[str] | None = None,
    max_papers: int | None = None,
) -> Counter[str]:
    """Export current unified manifests and verified PNGs without deleting existing artifacts."""
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    entries = parsed_index.load_index(
        service,
        parsed_folder_id,
        settings.index_file_name,
    )
    available = {
        paper_id: entry for paper_id, entry in entries.items() if entry.get("status") != "skipped"
    }

    if paper_ids is not None:
        missing = sorted(set(paper_ids) - available.keys())
        if missing:
            raise PapersNotFoundError(missing)
        selected = sorted(set(paper_ids))
    else:
        selected = sorted(available)

    if max_papers is not None:
        selected = selected[:max_papers]

    outcomes: Counter[str] = Counter()

    for paper_id in selected:
        content = drive_storage.download_file(
            service,
            str(available[paper_id]["json_file_id"]),
        )
        manifest = ParsedManifest.model_validate_json(content)
        if manifest.openalex_id != paper_id:
            raise ValueError(f"Indexed paper {paper_id} does not match its manifest")

        paper_dir = output_dir / manifest.openalex_id
        image_dir = paper_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)

        for image in manifest.images:
            png = drive_storage.download_file(service, image.file_id)
            if sha256(png).hexdigest() != image.sha256:
                raise ValueError(f"Image checksum mismatch for {image.figure_id}")

            pixmap = pymupdf.Pixmap(png)
            if (pixmap.width, pixmap.height) != (image.width, image.height):
                raise ValueError(f"Image dimension mismatch for {image.figure_id}")

            _write_atomic(image_dir / image.file_name, png)

        _write_atomic(paper_dir / "parsed.json", content)

        outcomes["exported"] += 1
        outcomes["figures"] += len(manifest.images)
        logger.info("Exported %s with %d figures", paper_id, len(manifest.images))

    return outcomes
