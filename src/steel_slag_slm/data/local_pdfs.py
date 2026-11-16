"""Resolve immutable local PDF inputs by their leading OpenAlex identifiers."""

import re
from pathlib import Path

from steel_slag_slm.data.drive_storage import parse_openalex_id


def select_local_pdfs(paper_ids: list[str], pdf_dir: Path) -> list[Path]:
    """Require one exact ID-prefix match per unique paper, preserving selection order."""
    if not paper_ids or len(set(paper_ids)) != len(paper_ids):
        raise ValueError("Select at least one paper without duplicate IDs")
    if any(re.fullmatch(r"W[0-9]+", paper_id) is None for paper_id in paper_ids):
        raise ValueError("Paper IDs must use the W-number format")
    candidates: dict[str, list[Path]] = {}
    for path in pdf_dir.glob("*.pdf"):
        paper_id = parse_openalex_id(path.name)
        if paper_id is not None and path.is_file():
            candidates.setdefault(paper_id, []).append(path)
    selected: list[Path] = []
    for paper_id in paper_ids:
        matches = candidates.get(paper_id, [])
        if len(matches) != 1:
            raise ValueError(f"{paper_id}: expected one PDF in {pdf_dir}, found {len(matches)}")
        selected.append(matches[0])
    return selected
