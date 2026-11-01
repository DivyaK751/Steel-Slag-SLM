"""Publish immutable figure assets and a validated paper manifest to Drive."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from steel_slag_slm.data import drive_storage
from steel_slag_slm.data.parsed_manifest import CONTENT_SCHEMA_VERSION, ParsedManifest
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.schemas import ParsedDocument


@dataclass(frozen=True, slots=True)
class PublishedParse:
    """Identify the immutable manifest and image assets for one successful publication."""

    manifest: ParsedManifest
    json_file_id: str
    image_file_ids: list[str]
    content: bytes


def publish_parse(
    service: Any,
    paper_folder_id: str,
    image_folder_id: str,
    source: PdfSource,
    pdf_bytes: bytes,
    parsed: ParsedDocument,
    revision: str,
) -> PublishedParse:
    """Upload generation-specific assets and manifest, without changing the root index.

    Leave existing artifacts untouched. A failed upload may leave unreferenced new artifacts;
    they are not published to readers until the caller successfully updates the index.
    """
    if parsed.images:
        raise ValueError("Unified publication must not receive legacy embedded images")

    generation = uuid4().hex
    references = []

    for position, item in enumerate(parsed.figures, start=1):
        figure = item.figure
        filename = f"{generation}_{item.file_name}"

        references.append(
            {
                "figure_id": f"{source.openalex_id}:{generation}:f{position}",
                "file_name": filename,
                "relative_path": f"images/{filename}",
                "file_id": "pending",
                "sha256": sha256(item.image_bytes).hexdigest(),
                "width": item.width,
                "height": item.height,
                "render_dpi": item.render_dpi,
                "figure_number": figure.figure_number,
                "caption": figure.caption,
                "figure_type": figure.figure_type,
                "type_confidence": figure.type_confidence,
                "pdf_text": item.pdf_text,
                "detector": "Docling",
                "renderer": "PyMuPDF",
                "placements": [
                    {
                        "page_number": figure.page_number,
                        "bbox": figure.bbox,
                        "coordinate_origin": "TOPLEFT",
                    }
                ],
                "caption_placements": [
                    {
                        "page_number": figure.caption_block.page_number,
                        "bbox": figure.caption_block.bbox,
                        "coordinate_origin": "TOPLEFT",
                    }
                ]
                if figure.caption_block is not None
                else [],
            }
        )

    manifest = ParsedManifest.model_validate(
        {
            "openalex_id": source.openalex_id,
            "source_drive_link": source.drive_link,
            "source_sha256": sha256(pdf_bytes).hexdigest(),
            "content_schema_version": CONTENT_SCHEMA_VERSION,
            "parser_revision": revision,
            "parse_generation": generation,
            "text_parser": parsed.text_parser,
            "image_parser": parsed.image_parser,
            "page_count": parsed.page_count,
            "markdown": parsed.markdown,
            "tables": list(parsed.tables),
            "content_blocks": [asdict(block) for block in parsed.content_blocks],
            "images": references,
        }
    )

    ids: list[str] = []
    for reference, item in zip(manifest.images, parsed.figures, strict=True):
        file_id = drive_storage.write_file(
            service,
            image_folder_id,
            reference.file_name,
            item.image_bytes,
            "image/png",
        )
        reference.file_id = file_id
        ids.append(file_id)

    manifest = ParsedManifest.model_validate(manifest.model_dump())
    content = manifest.model_dump_json(indent=2).encode("utf-8")
    json_file_id = drive_storage.write_file(
        service,
        paper_folder_id,
        f"parsed_{generation}.json",
        content,
        "application/json",
    )

    return PublishedParse(manifest, json_file_id, ids, content)
