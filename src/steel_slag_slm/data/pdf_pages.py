"""Cut a PDF down to its first pages and read their text layer."""

import pymupdf


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
