"""Split a parsed paper into section-aware chunks of text, tables and figures.

Papers with content blocks are chunked from the blocks, which carry page numbers. Each figure
chunk holds the caption plus Gemini's reading of the figure, and each table chunk holds the
caption, Gemini's description of the table and its compact rows. Older papers fall back to
their Markdown, which has no page numbers; their figures are indexed by caption only.
"""

import logging
import re
import textwrap
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace

from steel_slag_slm.data.table_text import compact_markdown_table, is_contents_list, is_table_row
from steel_slag_slm.rag.token_budget import TokenBudget
from steel_slag_slm.schemas import (
    Chunk,
    ChunkKind,
    FigureReading,
    PaperFigure,
    ParsedContentBlock,
    ParsedPaper,
    TableDescription,
)

logger = logging.getLogger(__name__)

IMAGE_PLACEHOLDER = "<!-- image -->"
# A line this many times or more is a running header or footer, so every copy is dropped
RUNNING_LINE_MIN_REPEATS = 3
RUNNING_LINE_MAX_CHARS = 120
# Docling labels for page furniture; they repeat on every page and carry no content
_SKIPPED_LABELS = frozenset({"page_header", "page_footer"})
# Publisher and repository notices that appear as body text on cover and journal pages
_BOILERPLATE = re.compile(
    r"accepted manuscript|digital repositor|digitalni repozitorij|archiving date|"
    r"datum arhiviranja|when citing, please refer|prosimo, da se pri navajanju|"
    r"original version of|izvirna različica|available online at|"
    r"creative commons|open access article|for peer review|"
    r"department of energy|office of scientific and technical information",
    re.IGNORECASE,
)
# A sentence up to this long is kept whole, even past the chunk size, so it is never cut.
# Limit set by the embedding model (512 tokens is roughly 2,000 characters)
MAX_WHOLE_SENTENCE_CHARS = 1500
# A section title longer than this is a running header merged into the heading; cut it short
MAX_SECTION_CHARS = 150
# A real section starts with its number ("3.2 Utilization…") or is one of these standard names
_NUMBERED_HEADING = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
_STANDARD_HEADINGS = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "literature review",
        "materials and methods",
        "materials and method",
        "methods",
        "methodology",
        "experimental",
        "experimental procedure",
        "results",
        "results and discussion",
        "discussion",
        "conclusion",
        "conclusions",
        "summary",
        "references",
        "bibliography",
        "acknowledgements",
        "acknowledgments",
        "appendix",
    }
)

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>\S.*?)\s*$")
_CAPTION = re.compile(r"^(Fig\.?|Figure|Slika|Abb\.?|Figura)\s*\d+", re.IGNORECASE)
# A sentence ends with terminal punctuation, optionally followed by a closing bracket or quote
_ENDS_SENTENCE = re.compile(r"[.!?:;)\]\"'”’%]\s*$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")

# A table's description takes at most this share of a chunk, so its rows still fit beside it
MAX_DESCRIPTION_SHARE = 0.5

# A block is ("text", paragraph) or ("table", index into ParsedPaper.tables)
_Block = tuple[str, str | int]
# A paragraph is its text and the pages it sits on (empty when the source has no pages)
Paragraph = tuple[str, tuple[int, ...]]
# A piece is one chunk before it is numbered: kind, section, text and pages
_Piece = tuple[ChunkKind, str, str, tuple[int, ...]]


def _is_repeatable(line: str) -> bool:
    """Return True for a plain short line; headings and table rows are never running lines."""
    stripped = line.strip()
    return (
        bool(stripped)
        and not is_table_row(line)
        and _HEADING.match(stripped) is None
        and len(stripped) <= RUNNING_LINE_MAX_CHARS
    )


def clean_lines(markdown: str) -> list[str]:
    """Drop image placeholders and running headers or footers from the Markdown lines."""
    lines = [line.rstrip() for line in markdown.splitlines()]
    lines = [line for line in lines if line.strip() != IMAGE_PLACEHOLDER]
    counts = Counter(line.strip() for line in lines if _is_repeatable(line))
    repeated = {text for text, count in counts.items() if count >= RUNNING_LINE_MIN_REPEATS}
    return [line for line in lines if not (_is_repeatable(line) and line.strip() in repeated)]


def scan_sections(lines: Iterable[str]) -> list[tuple[str, list[_Block]]]:
    """Group the lines into (heading, blocks) sections in reading order.

    Table rows are not text: each table becomes a block that holds its position in
    ParsedPaper.tables, so the tables can be matched to their section.
    """
    sections: list[tuple[str, list[_Block]]] = [("", [])]
    paragraph: list[str] = []
    table_index = 0
    in_table = False

    def flush_paragraph() -> None:
        if paragraph:
            sections[-1][1].append(("text", " ".join(paragraph)))
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if is_table_row(line):
            if not in_table:
                flush_paragraph()
                sections[-1][1].append(("table", table_index))
                table_index += 1
                in_table = True
            continue
        in_table = False
        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            sections.append((heading.group("title"), []))
        elif stripped:
            paragraph.append(stripped)
        else:
            flush_paragraph()
    flush_paragraph()
    return sections


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Break text into pieces of at most max_chars, at sentence boundaries where possible.

    A sentence longer than max_chars is the only case that is cut inside a sentence.
    """
    pieces: list[str] = []
    for sentence in _SENTENCE_BOUNDARY.split(text.strip()):
        if len(sentence) <= max(max_chars, MAX_WHOLE_SENTENCE_CHARS):
            pieces.append(sentence)
        else:
            pieces += textwrap.wrap(
                sentence, width=max_chars, break_long_words=True, break_on_hyphens=False
            )
    return pieces


def _continues_sentence(previous: str, following: str) -> bool:
    """True when a block stopped mid-sentence and the next block carries on lowercase."""
    return _ENDS_SENTENCE.search(previous) is None and following[:1].islower()


def pack_paragraphs(paragraphs: list[Paragraph], max_chars: int) -> list[Paragraph]:
    """Join paragraphs into passages of at most max_chars without splitting a paragraph.

    A passage's pages are the union of the pages of the paragraphs packed into it.
    """
    passages: list[Paragraph] = []
    current = ""
    current_pages: set[int] = set()
    for text, pages in paragraphs:
        for piece in split_long_text(text, max_chars):
            if current and len(current) + 1 + len(piece) > max_chars:
                passages.append((current, tuple(sorted(current_pages))))
                current = piece
                current_pages = set(pages)
            else:
                current = f"{current} {piece}".strip()
                current_pages |= set(pages)
    if current:
        passages.append((current, tuple(sorted(current_pages))))
    return passages


def _pack_lines(head: list[str], items: list[str], fits: Callable[[str], bool]) -> list[str]:
    """Group items into pieces that fit, each starting with the head lines.

    An item is never split: one too long for the budget gets a piece of its own.
    """
    pieces: list[str] = []
    group: list[str] = []
    for item in items:
        if group and not fits("\n".join([*head, *group, item])):
            pieces.append("\n".join([*head, *group]))
            group = []
        group.append(item)
    if group or not pieces:
        pieces.append("\n".join([*head, *group]))
    return pieces


def table_texts(
    table: str,
    description: TableDescription | None,
    max_chars: int,
    tokens: TokenBudget | None = None,
) -> list[str]:
    """Return a table as chunk texts: its caption, description, header row and compact rows.

    A table longer than max_chars, or than the token budget when one is given, is split into
    row groups that each repeat the caption, description and header, so every piece says
    what its numbers are.
    """
    lines = compact_markdown_table(table).splitlines()
    first_row = next((i for i, line in enumerate(lines) if is_table_row(line)), len(lines))
    caption = [line for line in lines[:first_row] if line.strip()]
    rows = [line for line in lines[first_row:] if line.strip()]
    described = _description_lines(
        description, _table_fits(max_chars, tokens, MAX_DESCRIPTION_SHARE)
    )
    return _pack_lines([*caption, *described, *rows[:1]], rows[1:], _table_fits(max_chars, tokens))


def _table_fits(
    max_chars: int, tokens: TokenBudget | None, share: float = 1.0
) -> Callable[[str], bool]:
    """Return a check that text takes at most this share of the character and token budgets."""
    return lambda text: (
        len(text) <= max_chars * share and (tokens is None or tokens.fits(text, share))
    )


def _description_lines(
    description: TableDescription | None, fits: Callable[[str], bool]
) -> list[str]:
    """Return a table description as lines, most useful first, that fit together.

    The summary is always kept; a later line that would no longer fit is left out.
    """
    if description is None:
        return []
    columns = [
        f"{column.label}: {column.meaning}" + (f" [{column.unit}]" if column.unit else "")
        for column in description.columns
        if column.meaning  # a column's label and unit are already in the header row
    ]
    candidates = [
        _labelled("Columns", columns, "; "),
        _labelled("Terms", [f"{term.term}: {term.meaning}" for term in description.terms], "; "),
        _labelled("Conditions", description.conditions, "; "),
        _labelled("Samples", description.samples, ", "),
        _labelled("Also called", description.synonyms, ", "),
    ]
    lines = [description.summary.strip()]
    for line in filter(None, candidates):
        if fits("\n".join([*lines, line])):
            lines.append(line)
    return [line for line in lines if line]


def _labelled(label: str, items: list[str], separator: str) -> str:
    return f"{label}: {separator.join(items)}" if items else ""


# Values are read off the plot image, so the chunk says they are approximate
_VALUES_NOTE = "Values read from the figure (approximate):"


def _figure_label(figure: PaperFigure) -> str:
    """Return the figure's caption, or "Fig. N" when the parser matched no caption."""
    if figure.caption.strip():
        return _clean_text(figure.caption)
    return f"Fig. {figure.figure_number}" if figure.figure_number else "Figure"


def _reading_lines(reading: FigureReading) -> tuple[list[str], list[str]]:
    """Return a reading as header lines and item lines; only the items are split across chunks."""
    header: list[str] = []
    items: list[str] = []
    if reading.columns and reading.rows:
        names = [f"{col.label} ({col.unit})" if col.unit else col.label for col in reading.columns]
        header = [_VALUES_NOTE, " | ".join(names)]
        items += [" | ".join(row) for row in reading.rows]
    if reading.phases:
        items.append(f"Phases: {', '.join(reading.phases)}")
    labels = {step.id: step.label for step in reading.steps}
    for step in reading.steps:
        conditions = f" ({'; '.join(step.conditions)})" if step.conditions else ""
        items.append(f"Step: {step.label}{conditions}")
    for flow in reading.flows:
        arrow = f"{labels.get(flow.source, flow.source)} -> {labels.get(flow.target, flow.target)}"
        items.append(f"{arrow} ({flow.label})" if flow.label else arrow)
    return header, items


def figure_texts(figure: PaperFigure, max_chars: int) -> list[str]:
    """Return a figure as chunk texts: its caption and summary, then values, phases or steps.

    A long reading is split into pieces that each repeat the caption, summary and column
    header, as long tables are. A figure Gemini has not read yet is its caption alone.
    """
    head = [_figure_label(figure)]
    items: list[str] = []
    if figure.reading is not None:
        header, items = _reading_lines(figure.reading)
        head += [figure.reading.summary.strip(), *header]
    return _pack_lines([line for line in head if line], items, lambda text: len(text) <= max_chars)


def _caption_number(caption: str) -> str:
    """Return the figure number a caption starts with ("3" for "Fig. 3a. ..."), or ""."""
    match = _FIGURE_REFERENCE.match(caption.strip())
    return match.group(1) if match else ""


def _take_figure(figures: list[PaperFigure], caption: str) -> PaperFigure | None:
    """Remove and return the first remaining figure with the caption's number, if any."""
    wanted = _caption_number(caption)
    if not wanted:
        return None
    for figure in figures:
        number = re.match(r"\d+", figure.figure_number or "")
        if number is not None and number.group() == wanted:
            figures.remove(figure)
            return figure
    return None


def _figure_pieces(section: str, figure: PaperFigure, max_chars: int) -> list[_Piece]:
    return [("figure", section, text, figure.pages) for text in figure_texts(figure, max_chars)]


def _is_caption(paragraph: str) -> bool:
    return _CAPTION.match(paragraph) is not None


def _text_passages(
    paragraphs: list[Paragraph], max_chars: int, detect_captions: bool = True
) -> list[tuple[ChunkKind, str, tuple[int, ...]]]:
    """Pack body paragraphs into passages; figure captions are kept as their own passages.

    Captions are found by their text only when detect_captions is set (Markdown papers, which
    have no labels). Content blocks carry a caption label, so they pass False.
    """
    passages: list[tuple[ChunkKind, str, tuple[int, ...]]] = []
    body: list[Paragraph] = []
    for paragraph in paragraphs:
        if detect_captions and _is_caption(paragraph[0]):
            passages += [("text", text, pages) for text, pages in pack_paragraphs(body, max_chars)]
            body = []
            passages += [
                ("figure", text, pages) for text, pages in pack_paragraphs([paragraph], max_chars)
            ]
        else:
            body.append(paragraph)
    passages += [("text", text, pages) for text, pages in pack_paragraphs(body, max_chars)]
    return passages


def _with_section(
    section: str, passages: list[tuple[ChunkKind, str, tuple[int, ...]]]
) -> list[_Piece]:
    return [(kind, section, text, pages) for kind, text, pages in passages]


_NUMBERED_HEADING_START = re.compile(
    r"^(\d+(?:\.\d+)*\.?)\s+("
    + "|".join(sorted((re.escape(name) for name in _STANDARD_HEADINGS), key=len, reverse=True))
    + r")\b(.*)$",
    re.IGNORECASE,
)


def _is_front_matter(block: ParsedContentBlock) -> bool:
    """True for a short early block of affiliations or contact details, not paper content."""
    return (
        len(block.text) <= FRONT_MATTER_MAX_CHARS
        and _AFFILIATION.search(block.text) is not None
        and all(p.page_number <= FRONT_MATTER_MAX_PAGE for p in block.placements)
    )


def _is_section_heading(text: str) -> bool:
    """Return True for a real section heading: numbered, or a standard name such as Results."""
    cleaned = " ".join(text.split())
    if _NUMBERED_HEADING.match(cleaned):
        return True
    return cleaned.strip(" .:").lower() in _STANDARD_HEADINGS


def _clean_text(text: str) -> str:
    """Join the text's words: drop soft hyphens (a word the parser split at a line end) and
    collapse whitespace."""
    return " ".join(re.sub(r"­\s*", "", text).split())


def _pages_of(block: ParsedContentBlock) -> tuple[int, ...]:
    return tuple(sorted({placement.page_number for placement in block.placements}))


def _pieces_from_blocks(
    paper: ParsedPaper, max_chars: int, table_max_chars: int, table_tokens: TokenBudget | None
) -> list[_Piece]:
    """Chunk a paper from its content blocks, which know their own pages and labels.

    Each caption block becomes the chunk of the figure with its number, in reading order.
    Figures whose caption is not in the text flow are added after the rest of the paper.
    Each table is led by its description, when Gemini has described it.
    """
    pieces: list[_Piece] = []
    section = ""
    paragraphs: list[Paragraph] = []
    seen_texts: set[str] = set()
    unplaced = list(paper.figures)
    placed_numbers: set[str] = set()
    for index, block in enumerate(paper.content_blocks):
        if block.label in _SKIPPED_LABELS or not block.text.strip():
            continue
        if block.kind == "table" and is_contents_list(block.text):
            continue  # a contents list the parser read as a table
        if block.kind == "text" and (
            _BOILERPLATE.search(block.text)
            or is_contents_list(block.text)
            or _is_front_matter(block)
        ):
            continue
        # A header that is not a real section (a title or declaration on the cover page) is
        # body text: attaching it as a section would label the text that follows it wrongly
        if block.label == "section_header" and _is_section_heading(block.text):
            pieces += _with_section(
                section, _text_passages(paragraphs, max_chars, detect_captions=False)
            )
            paragraphs = []
            section = _clean_text(block.text)[:MAX_SECTION_CHARS].rstrip()
        elif block.label == "caption" and _is_caption(block.text):
            pieces += _with_section(
                section, _text_passages(paragraphs, max_chars, detect_captions=False)
            )
            paragraphs = []
            number = _caption_number(block.text)
            figure = _take_figure(unplaced, block.text)
            if figure is None and number in placed_numbers:
                continue  # the parser repeated a caption whose figure is already placed
            placed_numbers.add(number)
            if figure is None:
                figure = PaperFigure(None, block.text, _pages_of(block), None)
            elif not figure.caption.strip() or not figure.pages:
                figure = replace(
                    figure,
                    caption=figure.caption or block.text,
                    pages=figure.pages or _pages_of(block),
                )
            pieces += _figure_pieces(section, figure, table_max_chars)
        elif block.kind == "table":
            # A sentence cut by the page layout can resume after the table, so keep its
            # unfinished last paragraph open for the next text block to continue
            held: list[Paragraph] = []
            if paragraphs and _ENDS_SENTENCE.search(paragraphs[-1][0]) is None:
                held, paragraphs = paragraphs[-1:], paragraphs[:-1]
            pieces += _with_section(
                section, _text_passages(paragraphs, max_chars, detect_captions=False)
            )
            paragraphs = held
            pages = _pages_of(block)
            description = paper.table_descriptions.get(index)
            pieces += [
                ("table", section, table, pages)
                for table in table_texts(block.text, description, table_max_chars, table_tokens)
            ]
        else:
            text = _clean_text(block.text)
            if text in seen_texts:
                continue  # a repeat of an earlier block in this paper
            seen_texts.add(text)
            heading = _NUMBERED_HEADING_START.match(text)
            if heading:
                pieces += _with_section(
                    section, _text_passages(paragraphs, max_chars, detect_captions=False)
                )
                paragraphs = []
                section = f"{heading.group(1)} {heading.group(2)}"
                text = heading.group(3).strip()
                if not text:
                    continue
            pages = _pages_of(block)
            if paragraphs and _continues_sentence(paragraphs[-1][0], text):
                # The parser split one sentence across blocks, so join them back together
                previous_text, previous_pages = paragraphs[-1]
                merged_pages = tuple(sorted(set(previous_pages) | set(pages)))
                paragraphs[-1] = (f"{previous_text} {text}", merged_pages)
            else:
                paragraphs.append((text, pages))
    pieces += _with_section(section, _text_passages(paragraphs, max_chars, detect_captions=False))
    for figure in unplaced:
        pieces += _figure_pieces("", figure, table_max_chars)
    return pieces


def _pieces_from_markdown(
    paper: ParsedPaper, max_chars: int, table_max_chars: int, table_tokens: TokenBudget | None
) -> list[_Piece]:
    """Chunk a paper from its Markdown. Used for papers parsed before content blocks."""
    pieces: list[_Piece] = []
    table_count = 0
    for section_title, blocks in scan_sections(clean_lines(paper.markdown)):
        paragraphs: list[Paragraph] = []
        for kind, value in blocks:
            if kind == "text":
                paragraphs.append((str(value), ()))
                continue
            pieces += _with_section(section_title, _text_passages(paragraphs, max_chars))
            paragraphs = []
            table_count += 1
            if int(value) < len(paper.tables):
                tables = table_texts(paper.tables[int(value)], None, table_max_chars, table_tokens)
                pieces += [("table", section_title, table, ()) for table in tables]
        pieces += _with_section(section_title, _text_passages(paragraphs, max_chars))

    if table_count != len(paper.tables):
        logger.warning(
            "%s: %d table blocks in the Markdown but %d tables in parsed.json",
            paper.openalex_id,
            table_count,
            len(paper.tables),
        )
    return pieces


def _carry_unfinished_sentences(pieces: list[_Piece]) -> list[_Piece]:
    """Move a sentence that a chunk ends in the middle of into the start of the next chunk.

    Only text chunks in the same section are joined this way. If the carried sentence would make
    the next chunk too long, the pieces are left as they are.
    """
    carried = list(pieces)
    for i in range(len(carried) - 1):
        kind, section, text, pages = carried[i]
        next_kind, next_section, next_text, next_pages = carried[i + 1]
        if kind != "text" or next_kind != "text" or section != next_section:
            continue
        if _ENDS_SENTENCE.search(text) is not None:
            continue
        boundaries = list(_SENTENCE_BOUNDARY.finditer(text))
        if not boundaries:
            continue  # the whole chunk is one unfinished sentence; nothing to carry cleanly
        cut = boundaries[-1]
        head, tail = text[: cut.start()], text[cut.end() :]
        merged = f"{tail} {next_text}"
        if len(merged) > MAX_WHOLE_SENTENCE_CHARS:
            continue
        carried[i] = (kind, section, head, pages)
        carried[i + 1] = (
            next_kind,
            next_section,
            merged,
            tuple(sorted(set(pages) | set(next_pages))),
        )
    return carried


def build_chunks(
    paper: ParsedPaper,
    max_chars: int,
    table_max_chars: int,
    paper_title: str = "",
    table_tokens: TokenBudget | None = None,
) -> list[Chunk]:
    """Turn one paper into chunks in reading order, each tagged with its section and pages.

    With table_tokens, a table chunk is also kept within that many tokens of the model that
    reads it (the reranker, which cuts its input off), not only within table_max_chars.

    Raises:
        ValueError: If max_chars or table_max_chars is not positive.
    """
    if max_chars <= 0 or table_max_chars <= 0:
        raise ValueError("chunk sizes must be greater than zero")

    if paper.content_blocks:
        pieces = _pieces_from_blocks(paper, max_chars, table_max_chars, table_tokens)
    else:
        pieces = _pieces_from_markdown(paper, max_chars, table_max_chars, table_tokens)
    pieces = _drop_repeated(_carry_unfinished_sentences(pieces))
    chunks = [
        Chunk(
            paper_id=paper.openalex_id,
            kind=kind,
            section=section,
            text=text,
            position=position,
            pages=pages,
            paper_title=paper_title,
        )
        for position, (kind, section, text, pages) in enumerate(pieces)
    ]
    return _link_figure_captions(chunks)


def _passage(chunk: Chunk, with_title: bool) -> str:
    title = chunk.paper_title if with_title else ""
    parts = [title, chunk.section, chunk.text, *chunk.figure_captions]
    return "\n".join(part for part in parts if part)


def passage_for_embedding(chunk: Chunk) -> str:
    """Return the text to embed and keyword-index: the section heading, the chunk text, then
    the captions of the figures it mentions.

    A table also starts with its paper's title: its cells are numbers and abbreviations, so
    without the title it shares almost no words with a question about its study.
    """
    return _passage(chunk, with_title=chunk.kind == "table")


def passage_for_reranking(chunk: Chunk) -> str:
    """Return the text a reranker reads: the paper's title, then the passage, so the reranker
    can tell which study each chunk comes from."""
    return _passage(chunk, with_title=True)


_AFFILIATION = re.compile(
    r"@|\b\d{4,5}\s+[A-ZČŠŽ][a-zčšž]+|\b(university|universita|univerza|faculty|fakulteta|institute|department|college|e-mail|izjav|declaration)\b",
    re.IGNORECASE,
)
FRONT_MATTER_MAX_CHARS = 300
FRONT_MATTER_MAX_PAGE = 2


_FIGURE_REFERENCE = re.compile(r"\b(?:Fig\.?|Figure|Slika|Abb\.?|Figura)\s*(\d+)", re.IGNORECASE)


def _drop_repeated(pieces: list[_Piece]) -> list[_Piece]:
    """Keep the first copy of each text passage in a paper; later exact repeats are dropped."""
    seen: set[str] = set()
    kept: list[_Piece] = []
    for piece in pieces:
        if piece[0] == "text":
            key = " ".join(piece[2].split())
            if key in seen:
                continue
            seen.add(key)
        kept.append(piece)
    return kept


def _link_figure_captions(chunks: list[Chunk]) -> list[Chunk]:
    """Attach each figure's caption to the text chunks that mention that figure number."""
    captions: dict[str, str] = {}
    for chunk in chunks:
        if chunk.kind == "figure":
            # Only the caption line is linked; the reading stays in the figure's own chunk
            caption = chunk.text.strip().split("\n", 1)[0]
            match = _FIGURE_REFERENCE.match(caption)
            if match:
                captions.setdefault(match.group(1), caption)
    linked: list[Chunk] = []
    for chunk in chunks:
        if chunk.kind != "text":
            linked.append(chunk)
            continue
        numbers = dict.fromkeys(m.group(1) for m in _FIGURE_REFERENCE.finditer(chunk.text))
        found = tuple(captions[n] for n in numbers if n in captions)
        linked.append(replace(chunk, figure_captions=found) if found else chunk)
    return linked
