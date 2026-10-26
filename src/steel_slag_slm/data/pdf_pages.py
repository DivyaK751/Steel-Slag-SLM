"""Cut a PDF down to its first pages, read their text layer, and render or read regions."""

import pymupdf

from steel_slag_slm.schemas import PageBox

# PDF coordinates are in points: 72 per inch
POINTS_PER_INCH = 72


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
