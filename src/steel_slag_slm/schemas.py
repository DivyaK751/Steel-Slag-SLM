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


# Controlled vocabulary of the CNS input workbook ("10 Controlled Vocab" sheet)
SteelSlagType = Literal[
    "EAF", "BOF", "Ladle", "Stainless Steel", "Electric Arc Furnace Dust-derived",
    "Other Steel Slag",
]  # fmt: skip
PublicationType = Literal[
    "Journal Article", "Conference Paper", "Thesis/Dissertation", "Patent", "Government Report",
    "Technical Report", "Dataset", "Standard", "Other",
]  # fmt: skip
ExtractionConfidence = Literal["High", "Medium", "Low"]
# The workbook sheets that hold experimental data (sheet 1 is per paper, sheet 9 is provenance)
DataSheet = Literal[
    "sheet_02_feedstock_chemistry", "sheet_03_processing", "sheet_04_particle_size",
    "sheet_05_activation", "sheet_06_concrete_performance", "sheet_07_durability",
    "sheet_08_carbon_uptake",
]  # fmt: skip


@dataclass(frozen=True, slots=True)
class PaperCitation:
    """Bibliographic fields of a paper, taken from the registry rather than the model."""

    title: str | None
    authors: tuple[str, ...]
    year: int | None
    doi: str | None


class ExtractedLiterature(BaseModel):
    """Paper-level fields of the Scientific Literature sheet that come from the paper's text."""

    publication_type: PublicationType | None
    journal_or_source: str | None
    country_or_region: str | None
    study_objective: str | None
    abstract_or_key_finding: str | None
    full_text_access: Literal["Open", "Abstract Only", "Restricted", "Unknown"] | None
    relevant_to_scm: Literal["Yes", "No", "Unclear"]
    notes: str | None


class ExtractedMaterial(BaseModel):
    """One distinct steel slag in the paper; every fact about it carries its material_id."""

    material_id: str  # MAT-0001, MAT-0002, ... within this paper
    steel_slag_type: SteelSlagType
    description: str


class ExtractedFact(BaseModel):
    """One value the paper reports, destined for one column of one data sheet."""

    sheet: DataSheet
    field: str  # workbook column header, e.g. "CaO_pct"
    value: str  # in the column's unit, e.g. "20" for a reported 0.20 mass fraction
    reported_value: str  # exactly as printed, with its unit
    material_id: str
    experiment_id: str | None
    mix_id: str | None
    location: str  # page, table or figure
    excerpt: str
    confidence: ExtractionConfidence


class PaperExtraction(BaseModel):
    """Gemini's answer when asked for every workbook value one paper reports."""

    literature: ExtractedLiterature
    materials: list[ExtractedMaterial]
    facts: list[ExtractedFact]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens one Gemini call used, as Vertex bills them."""

    input_tokens: int
    cached_tokens: int  # part of input_tokens, billed at the lower cached rate
    output_tokens: int  # includes thinking tokens, which are billed as output
