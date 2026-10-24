"""Record types shared across modules."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

HIGH_CONFIDENCE_MIN_SCORE = 0.85
MEDIUM_CONFIDENCE_MIN_SCORE = 0.60


@dataclass(frozen=True, slots=True)
class Paper:
    """One OpenAlex work, reduced to the fields the pipeline uses."""

    openalex_id: str  # short form, e.g. "W2068066265"
    title: str
    abstract: str
    doi: str | None
    year: int | None
    free_pdf_url: str | None  # publisher or repository copy, costs nothing
    paid_pdf_url: str | None  # OpenAlex cached copy, billed per download
    authors: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    language: str | None = None  # ISO 639-1 code from OpenAlex, e.g. "en"


@dataclass(frozen=True, slots=True)
class ImagePlacement:
    """Locate an image resource on a source PDF page."""

    page_number: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class ParsedImage:
    """Describe one extracted image resource and its source-page placements."""

    xref: int
    file_name: str
    width: int
    height: int
    placements: tuple[ImagePlacement, ...]
    image_bytes: bytes


@dataclass(frozen=True, slots=True)
class ContentPlacement:
    """Locate a parsed text or table block on a source PDF page."""

    page_number: int
    bbox: tuple[float, float, float, float]
    coordinate_origin: str


@dataclass(frozen=True, slots=True)
class ParsedContentBlock:
    """Store one parsed text or table item with its source-page placements."""

    kind: Literal["text", "table"]
    label: str
    text: str
    placements: tuple[ContentPlacement, ...]


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Store text, tables, and image resources parsed from one PDF."""

    text_parser: str
    image_parser: str
    page_count: int
    markdown: str
    tables: tuple[str, ...]
    images: tuple[ParsedImage, ...]
    content_blocks: tuple[ParsedContentBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedPaper:
    """The text and tables of one paper, read back from its parsed.json in Drive."""

    openalex_id: str
    markdown: str
    tables: tuple[str, ...]
    # Empty for papers parsed before content blocks existed; the Markdown is used then
    content_blocks: tuple[ParsedContentBlock, ...] = ()


ChunkKind = Literal["text", "table", "figure_caption"]


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable piece of a paper, with the section and pages it came from."""

    paper_id: str
    kind: ChunkKind
    section: str  # heading the text sits under; "" before the first heading
    text: str
    position: int  # order within the paper, so neighbours can be found
    pages: tuple[int, ...] = ()  # PDF pages the text sits on; () when unknown
    figure_captions: tuple[str, ...] = ()  # captions of the figures this text mentions


@dataclass(frozen=True, slots=True)
class PaperForScreening:
    """One row of the BigQuery papers table, reduced to what the screening agent reads."""

    openalex_id: str
    title: str
    abstract: str
    year: int | None
    keywords: tuple[str, ...]


def confidence_for_score(score: float) -> Literal["High", "Medium", "Low"]:
    """Map a 0-1 score to the confidence band the screening prompt defines."""
    if score >= HIGH_CONFIDENCE_MIN_SCORE:
        return "High"
    if score >= MEDIUM_CONFIDENCE_MIN_SCORE:
        return "Medium"
    return "Low"


class RelevanceDecision(BaseModel):
    """The screening agent's verdict on one paper; also the agent's output schema."""

    openalex_id: str = ""
    relevant: Literal["Yes", "No", "Unclear"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    relevance_confidence: Literal["High", "Medium", "Low"]
    relevance_reason: str
    supporting_excerpt: str | None = None

    @model_validator(mode="after")
    def _confidence_follows_score(self) -> "RelevanceDecision":
        # The model sometimes picks a band that contradicts its own score; the score wins
        self.relevance_confidence = confidence_for_score(self.relevance_score)
        return self


@dataclass(frozen=True, slots=True)
class PaperForAbstract:
    """A paper whose abstract is missing, and the Drive file holding its PDF."""

    openalex_id: str
    title: str
    drive_file_id: str


class ExtractedAbstract(BaseModel):
    """Gemini's answer when asked for the abstract printed on a paper's first pages."""

    found: bool
    abstract: str | None = None
    heading: str | None = None  # e.g. "Abstract" or "Summary", as printed
    language: str | None = None  # ISO 639-1 code of the abstract text
