"""Render contextual rows from Markdown already saved in approved parses."""

import json
from re import fullmatch

from docling_core.types.doc.base import BoundingBox, CoordOrigin
from docling_core.types.doc.common.reference import ProvenanceItem

from steel_slag_slm.data.parsed_manifest import ContentBlock, ParsedManifest
from steel_slag_slm.data.table_evidence import EvidenceRow, RenderingIssue, TableEvidence


def _cells(line: str) -> list[str]:
    if not (line.startswith("|") and line.endswith("|")) or any(
        mark in line for mark in ("\\", "`")
    ):
        return []
    return [cell.strip() for cell in line[1:-1].split("|")]


def _render_table(block: ContentBlock, index: int) -> TableEvidence:
    result = TableEvidence(
        reference=f"approved-markdown:{index}", provenance=[], rows=[], issues=[]
    )
    caption, _, body = block.text.strip().partition("\n")
    lines = [line.strip() for line in body.strip().splitlines()]
    if not caption.startswith("Table ") or len(lines) < 3 or len(block.placements) != 1:
        result.issues.append(RenderingIssue(reason="Missing caption, grid, or single placement"))
        return result

    headers, divider = _cells(lines[0]), _cells(lines[1])
    if (
        len(headers) < 2
        or len(divider) != len(headers)
        or any(not fullmatch(r":?-{3,}:?", cell) for cell in divider)
        or not all(headers[1:])
        or len({header.casefold() for header in headers}) != len(headers)
    ):
        result.issues.append(RenderingIssue(reason="Unsupported Markdown header"))
        return result

    placement = block.placements[0]
    left, top, right, bottom = placement.bbox
    result.provenance.append(
        ProvenanceItem(
            page_no=placement.page_number,
            bbox=BoundingBox(
                l=left,
                t=top,
                r=right,
                b=bottom,
                coord_origin=CoordOrigin(placement.coordinate_origin),
            ),
            charspan=(0, 0),
        )
    )
    for row_index, line in enumerate(lines[2:]):
        values = _cells(line)
        if len(values) != len(headers) or not all(values):
            result.issues.append(
                RenderingIssue(row_index=row_index, reason="Unsupported or incomplete Markdown row")
            )
            continue

        fields = [
            f"{header}: {value}" if header else value
            for header, value in zip(headers, values, strict=True)
        ]
        # Saved Markdown has no native cell indices.
        result.rows.append(
            EvidenceRow(row_index=row_index, cell_indices=[], text="\n".join([caption, *fields]))
        )
    return result


def enrich_approved_json(content: bytes) -> bytes:
    """Validate an approved manifest and return a copy with only table evidence added.

    Reject manifests with existing table evidence rather than overwriting it.
    """
    original = json.loads(content)
    manifest = ParsedManifest.model_validate(original)
    if "contextual_tables" in original:
        raise ValueError("Manifest already has contextual_tables; use the approved baseline")
    tables = [
        _render_table(block, index)
        for index, block in enumerate(manifest.content_blocks)
        if block.kind == "table"
    ]
    output = {**original, "contextual_tables": [table.model_dump(mode="json") for table in tables]}
    encoded = (json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    ParsedManifest.model_validate_json(encoded)
    return encoded
