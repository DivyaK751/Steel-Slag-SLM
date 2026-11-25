"""Validate the version-four parsing manifest shared by publishers, figure and table readers."""

import json
from hashlib import sha256
from importlib.metadata import version
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.schemas import FigureReading, TableDescription

CONTENT_SCHEMA_VERSION = 4
PIPELINE_REVISION = "unified_pdf_v1"
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Generation = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
# Written by the retired per-row table experiment; table descriptions replace it
RETIRED_FIELDS = frozenset({"contextual_tables"})


class Placement(BaseModel):
    """Locate an item in physical PDF pages and PDF-point coordinates."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    page_number: PositiveInt
    bbox: tuple[float, float, float, float]
    coordinate_origin: Literal["TOPLEFT", "BOTTOMLEFT"]

    @model_validator(mode="after")
    def validate_box(self) -> "Placement":
        x0, y0, x1, y1 = self.bbox
        valid_y = y1 > y0 if self.coordinate_origin == "TOPLEFT" else y0 > y1
        if x1 <= x0 or not valid_y:
            raise ValueError("Bounding box must have positive width and height")
        return self


class ContentBlock(BaseModel):
    """Validate text and table provenance without changing the legacy content shape."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "table"]
    label: str
    text: str
    placements: list[Placement]


class FigureReference(BaseModel):
    """Reference an immutable PNG and the figure and caption locations it represents."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    figure_id: str
    file_name: str
    relative_path: str
    # None when the run kept JSON only; the PNG can be re-rendered from placements and render_dpi
    file_id: Annotated[str, Field(min_length=1)] | None
    sha256: Digest
    width: PositiveInt
    height: PositiveInt
    render_dpi: PositiveInt
    figure_number: str | None
    caption: str
    figure_type: str
    type_confidence: Annotated[float, Field(ge=0, le=1)]
    pdf_text: str
    detector: Literal["Docling"]
    renderer: Literal["PyMuPDF"]
    placements: Annotated[list[Placement], Field(min_length=1, max_length=1)]
    caption_placements: Annotated[list[Placement], Field(max_length=1)]
    # What Gemini read off the figure (e.g. a plot's values as a table); None until it is read
    reading: FigureReading | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "FigureReference":
        path = PurePosixPath(self.relative_path)
        if (
            "/" in self.file_name
            or "\\" in self.file_name
            or path.parts != ("images", self.file_name)
            or self.relative_path != f"images/{self.file_name}"
            or not self.file_name.endswith(".png")
        ):
            raise ValueError("Figure path must name a PNG directly under images/")

        if any(
            placement.coordinate_origin != "TOPLEFT"
            for placement in self.placements + self.caption_placements
        ):
            raise ValueError("Figure and caption boxes must use TOPLEFT coordinates")

        if self.caption_placements and (
            self.caption_placements[0].page_number != self.placements[0].page_number
        ):
            raise ValueError("Matched caption must be on the figure's page")

        return self


class StoredTableDescription(BaseModel):
    """A table's description, tied to the exact table text and prompt it was made from."""

    model_config = ConfigDict(extra="forbid")

    block_index: Annotated[int, Field(ge=0, strict=True)]  # position in content_blocks
    table_sha256: Digest  # of the table block's text, so a reparsed table is never mislabelled
    prompt_version: PositiveInt
    description: TableDescription
    # "gemini": Gemini's answer, which passed every check. "paper": no answer passed, so the
    # description holds only the paper's own words (caption, headers) and adds nothing to search
    source: Literal["gemini", "paper"] = "gemini"

    @model_serializer(mode="wrap")
    def _write_source_only_when_paper(self, handler: SerializerFunctionWrapHandler) -> Any:
        # A missing source means "gemini", so code written before the field existed still
        # reads nearly every description; it skips a "paper" one as unreadable, as intended
        data = handler(self)
        if data.get("source") == "gemini":
            del data["source"]
        return data


class ParsedManifest(BaseModel):
    """Validate a complete unified parse before publication or figure consumption."""

    model_config = ConfigDict(extra="forbid")

    openalex_id: Annotated[str, Field(pattern=r"^W\d+$")]
    source_drive_link: str
    source_sha256: Digest
    content_schema_version: Literal[4]
    parser_revision: Digest
    parse_generation: Generation
    text_parser: Literal["Docling"]
    image_parser: Literal["Docling+PyMuPDF"]
    page_count: PositiveInt
    markdown: str
    tables: list[str]
    content_blocks: list[ContentBlock]
    images: list[FigureReference]
    # Gemini's descriptions of the table blocks, one per described table
    table_descriptions: list[StoredTableDescription] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_retired_fields(cls, data: Any) -> Any:
        """Read manifests the retired row backfill wrote, without keeping its rows."""
        if isinstance(data, dict) and not RETIRED_FIELDS.isdisjoint(data):
            return {key: value for key, value in data.items() if key not in RETIRED_FIELDS}
        return data

    @model_validator(mode="after")
    def validate_locations(self) -> "ParsedManifest":
        placements = [placement for block in self.content_blocks for placement in block.placements]
        placements += [
            placement
            for image in self.images
            for placement in image.placements + image.caption_placements
        ]
        if any(placement.page_number > self.page_count for placement in placements):
            raise ValueError("Placement page exceeds page_count")

        ids = [image.figure_id for image in self.images]
        names = [image.file_name for image in self.images]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("Figure IDs and filenames must be unique within a paper")

        for position, image in enumerate(self.images, start=1):
            expected_id = f"{self.openalex_id}:{self.parse_generation}:f{position}"
            if image.figure_id != expected_id:
                raise ValueError("Figure ID must identify its paper, generation and position")
            if not image.file_name.startswith(f"{self.parse_generation}_"):
                raise ValueError("Figure filename must identify its parse generation")

        return self

    @model_validator(mode="after")
    def validate_table_descriptions(self) -> "ParsedManifest":
        indices = [stored.block_index for stored in self.table_descriptions]
        if len(set(indices)) != len(indices):
            raise ValueError("A table can have only one description")
        for stored in self.table_descriptions:
            if stored.block_index >= len(self.content_blocks):
                raise ValueError("Table description points past the content blocks")
            block = self.content_blocks[stored.block_index]
            if block.kind != "table" or table_text_sha256(block.text) != stored.table_sha256:
                raise ValueError("Table description does not match its table block")
        return self


def table_text_sha256(text: str) -> str:
    """Fingerprint a table block's text, to tell whether a description still belongs to it."""
    return sha256(text.encode("utf-8")).hexdigest()


def has_retired_fields(content: bytes) -> bool:
    """Return whether a parsed.json still holds fields that a rewrite should remove."""
    record = json.loads(content)
    return isinstance(record, dict) and not RETIRED_FIELDS.isdisjoint(record)


def parser_revision(settings: FigureExtractionSettings) -> str:
    """Fingerprint extraction settings and installed parser versions, excluding output paths."""
    payload = {
        "pipeline": PIPELINE_REVISION,
        "docling": version("docling"),
        "docling_core": version("docling-core"),
        "pymupdf": version("pymupdf"),
        "settings": settings.model_dump(mode="json", exclude={"output_dir"}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()
