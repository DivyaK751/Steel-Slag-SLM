"""Record types shared across modules."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Bands from prompts/screen_relevance.yaml (v2): the score is P(relevant), so a score near
# either end is a confident decision and the middle is not
HIGH_CONFIDENCE_MIN_SCORE = 0.85
HIGH_CONFIDENCE_MAX_SCORE = 0.15
MEDIUM_CONFIDENCE_MIN_SCORE = 0.70
MEDIUM_CONFIDENCE_MAX_SCORE = 0.30  # exclusive


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
    """Store parsed text, tables, legacy image resources and optional figure crops."""

    text_parser: str
    image_parser: str
    page_count: int
    markdown: str
    tables: tuple[str, ...]
    images: tuple[ParsedImage, ...]
    content_blocks: tuple[ParsedContentBlock, ...] = ()
    figures: tuple["RenderedFigure", ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedPaper:
    """The text and tables of one paper, read back from its parsed.json in Drive."""

    openalex_id: str
    markdown: str
    tables: tuple[str, ...]
    # Empty for papers parsed before content blocks existed; the Markdown is used then
    content_blocks: tuple[ParsedContentBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens one or more model calls used, as reported by the provider."""

    input_tokens: int = 0  # prompt text plus attachments such as images
    cached_tokens: int = 0  # part of input_tokens billed at the cached rate
    output_tokens: int = 0  # includes thinking for calls made through the shared runner
    thinking_tokens: int = 0  # separately reported reasoning for legacy callers only

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
        )


# x0, y0, x1, y1 in PDF points, measured from the page's top-left corner
PageBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class CaptionBlock:
    """A "Fig. N ..." text block on a page, used to name the figure above or below it."""

    page_number: int
    bbox: PageBox
    text: str
    figure_number: str  # as printed after "Fig."/"Figure", e.g. "3" or "3a"


@dataclass(frozen=True, slots=True)
class Figure:
    """Store a figure region and its matched caption metadata.
    caption_block is None when no exact caption block is available, including captions
    recovered from the figure region's text layer.
    """

    page_number: int
    bbox: PageBox
    figure_type: str
    type_confidence: float
    caption: str = ""
    figure_number: str | None = None
    caption_block: CaptionBlock | None = None


@dataclass(frozen=True, slots=True)
class RenderedFigure:
    """Store a figure crop and its source metadata before artifact publication.
    Width and height are PNG pixels; the figure's boxes are top-left PDF points.
    pdf_text is the source PDF text layer inside the crop, not OCR or model output.
    """

    figure: Figure
    file_name: str
    width: int
    height: int
    render_dpi: int
    pdf_text: str
    image_bytes: bytes


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
    parse_generation: str = ""  # the parse this chunk came from; "" when it was not recorded


@dataclass(frozen=True, slots=True)
class PaperForScreening:
    """One row of the BigQuery papers table, reduced to what the screening agent reads."""

    openalex_id: str
    title: str
    abstract: str
    year: int | None
    keywords: tuple[str, ...]


def confidence_for_score(score: float) -> Literal["High", "Medium", "Low"]:
    """Map a 0-1 relevance probability to the confidence band the screening prompt defines."""
    if score >= HIGH_CONFIDENCE_MIN_SCORE or score <= HIGH_CONFIDENCE_MAX_SCORE:
        return "High"
    if score >= MEDIUM_CONFIDENCE_MIN_SCORE or score < MEDIUM_CONFIDENCE_MAX_SCORE:
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


@dataclass(frozen=True, slots=True)
class FigureToRead:
    """Store one local figure and optional immutable-source identity for model reading."""

    paper_id: str
    image_path: Path
    figure_number: str | None
    caption: str
    pdf_text: str  # words the PDF stores inside the figure; "" for pasted-in pictures
    detected_type: str  # Docling's label, e.g. "line_chart"
    figure_id: str | None = None
    image_sha256: str | None = None


FigureKind = Literal["data_plot", "xrd_pattern", "process_diagram", "micrograph_or_photo", "other"]


# Where in the figure a table column's name was printed
ColumnSource = Literal["x_axis", "y_axis", "legend", "label", "other"]


class TableColumn(BaseModel):
    """One column of the values read off a figure, named exactly as printed."""

    label: str
    unit: str | None = None
    source: ColumnSource
    # First and last tick values when the column is a numeric axis; None otherwise
    axis_min: float | None = None
    axis_max: float | None = None


class ProcessStep(BaseModel):
    """One box or stage of a process diagram, with any conditions printed in it."""

    id: str
    label: str
    conditions: list[str] = Field(default_factory=list)


class ProcessFlow(BaseModel):
    """An arrow between two steps of a process diagram."""

    source: str
    target: str
    label: str | None = None


class FigureReading(BaseModel):
    """Gemini's reading of one figure as printed; values read off plots are approximate.

    Plots are read as a table: columns named as on the axes and legend, one row per value.
    """

    figure_type: FigureKind
    summary: str
    columns: list[TableColumn] = Field(default_factory=list)
    # One list per row, a cell per column, copied as printed (e.g. "12-Hour", "10.3")
    rows: list[list[str]] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)  # phases labelled on an XRD pattern
    steps: list[ProcessStep] = Field(default_factory=list)
    flows: list[ProcessFlow] = Field(default_factory=list)


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
