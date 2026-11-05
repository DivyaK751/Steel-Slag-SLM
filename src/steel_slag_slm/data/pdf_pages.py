"""Cut a PDF down to its first pages, read their text layer, and render, read or mark regions."""

from dataclasses import dataclass

import pymupdf

from steel_slag_slm.schemas import PageBox

# PDF coordinates are in points: 72 per inch
POINTS_PER_INCH = 72
# Box labels are small so they don't hide the text they mark
LABEL_FONT_SIZE = 6


@dataclass(frozen=True, slots=True)
class BoxAnnotation:
    """A labelled rectangle to draw on one page of a PDF."""

    page_number: int  # starting at 1
    bbox: PageBox
    label: str
    color: tuple[float, float, float]  # RGB, each 0 to 1
    line_width: float
    # Inside the box's bottom edge instead of above it, so two labels on one box don't overlap
    label_at_bottom: bool = False


def first_pages(pdf: bytes, page_count: int) -> bytes:
    """Return a new PDF holding only the first page_count pages (fewer if the PDF is shorter).

    Raises:
        pymupdf.FileDataError: If the bytes are not a readable PDF.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as source, pymupdf.open() as excerpt:
        excerpt.insert_pdf(source, from_page=0, to_page=min(page_count, source.page_count) - 1)
        return bytes(excerpt.tobytes())


def page_text(pdf: bytes) -> str:
    """Return the text layer of every page, which is empty for scanned (image-only) pages."""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def render_regions(pdf: bytes, regions: list[tuple[int, PageBox]], dpi: int) -> list[bytes]:
    """Render each (page number, box) region of the PDF as PNG bytes, in the order given.

    Rendering from the page, rather than copying embedded image files, also captures vector
    plots and keeps multi-part figures whole. Page numbers start at 1; boxes use a top-left
    origin in points.

    Raises:
        pymupdf.FileDataError: If the bytes are not a readable PDF.
    """
    zoom = dpi / POINTS_PER_INCH
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return [
            document[page_number - 1]
            .get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(*bbox))
            .tobytes("png")
            for page_number, bbox in regions
        ]


def region_text(pdf: bytes, regions: list[tuple[int, PageBox]]) -> list[str]:
    """Return the text layer inside each (page number, box) region, in the order given.

    Vector figures keep their axis numbers, labels and legends as real text, so this reads
    them exactly; a figure pasted in as a picture has none and gives "".

    Raises:
        pymupdf.FileDataError: If the bytes are not a readable PDF.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return [
            " ".join(document[page_number - 1].get_text("text", clip=pymupdf.Rect(*bbox)).split())
            for page_number, bbox in regions
        ]


def draw_boxes(pdf: bytes, boxes: list[BoxAnnotation]) -> bytes:
    """Return a copy of the PDF with each box outlined on its page and its label written beside it.

    Raises:
        pymupdf.FileDataError: If the bytes are not a readable PDF.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for box in boxes:
            page = document[box.page_number - 1]
            rect = pymupdf.Rect(*box.bbox)
            page.draw_rect(rect, color=box.color, width=box.line_width)
            if box.label_at_bottom:
                label_y = rect.y1 - 2
            # Above the box, or just inside it when the box touches the top of the page
            elif rect.y0 > LABEL_FONT_SIZE + 2:
                label_y = rect.y0 - 2
            else:
                label_y = rect.y0 + LABEL_FONT_SIZE
            page.insert_text(
                (rect.x0 + 1, label_y), box.label, fontsize=LABEL_FONT_SIZE, color=box.color
            )
        return bytes(document.tobytes())
