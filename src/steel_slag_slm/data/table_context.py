"""Pick the tables of a parsed paper that need a description, with the text that explains them.

A table's own cells rarely say what its symbols, codes and units mean; the paper says it
elsewhere: in notes under the table, in the sentence that cites it, or where a material code
is first defined ("steel slag powder (SSD)"). Those paragraphs are what the describer reads.
"""

import re
from collections.abc import Sequence

from steel_slag_slm.data.parsed_manifest import ContentBlock, ParsedManifest, table_text_sha256
from steel_slag_slm.data.section_headings import MAX_SECTION_CHARS, is_section_heading
from steel_slag_slm.data.table_text import (
    compact_markdown_table,
    is_contents_list,
    table_caption,
    table_label,
    table_rows,
)
from steel_slag_slm.schemas import TableToDescribe

# Notes Docling places right after a table hold its symbol and unit definitions
_NOTE_LABELS = frozenset({"footnote"})
_PROSE_LABELS = frozenset({"text", "list_item", "caption", "footnote", "formula"})
# A header or row label this short is a code or symbol the paper defines somewhere, not prose
_MAX_TERM_CHARS = 12
_CODE = re.compile(r"[^\W\d_]")  # holds at least one letter
_ASCII_WORD = re.compile(r"[A-Za-z][a-z]+")  # a plain word such as "Mix" or "Sample"
_UNIT = re.compile(r"\s*\([^)]*\)")  # a parenthesised unit such as "(MPa)" or "(%)"
_FURNITURE_LABELS = frozenset({"page_header", "page_footer"})
# Blocks that can sit between a table and its continuation on the next page
_BETWEEN_PAGES_LABELS = _FURNITURE_LABELS | {"caption", "footnote"}


def tables_to_describe(
    manifest: ParsedManifest, prompt_version: int, context_max_chars: int, paper_title: str = ""
) -> list[TableToDescribe]:
    """Return the paper's tables that lack a description from this prompt version.

    Contents lists that the parser read as tables are left out, as the search index leaves
    them out. Each table carries the paper's title, its section heading, up to
    context_max_chars of the paper's explaining text, and the table it continues, if any.
    """
    current = {
        stored.block_index
        for stored in manifest.table_descriptions
        if stored.prompt_version == prompt_version
    }
    blocks = manifest.content_blocks
    return [
        TableToDescribe(
            paper_id=manifest.openalex_id,
            block_index=index,
            label=_label_of(blocks, index),
            table=compact_markdown_table(block.text),
            context=table_context(blocks, index, context_max_chars),
            table_sha256=table_text_sha256(block.text),
            paper_title=paper_title,
            section=_section_of(blocks, index),
            previous_table=_continued_table(blocks, index),
        )
        for index, block in enumerate(blocks)
        if block.kind == "table"
        and block.text.strip()
        and not is_contents_list(block.text)
        and index not in current
    ]


def table_context(blocks: Sequence[ContentBlock], index: int, max_chars: int) -> str:
    """Join the paragraphs that explain the table at blocks[index], most telling first.

    In order: notes right after the table, the paragraph just before it, paragraphs citing
    its "Table N", then paragraphs defining its codes or symbols. Whole paragraphs only, in
    that order, until max_chars is reached.
    """
    table = blocks[index].text
    candidates = [
        *_notes_after(blocks, index),
        *_paragraph_before(blocks, index),
        *_citing_paragraphs(blocks, _label_of(blocks, index)),
        *_defining_paragraphs(blocks, _table_terms(table)),
    ]
    chosen: list[str] = []
    used = 0
    for text in dict.fromkeys(candidates):  # first occurrence keeps its priority
        if text in table:
            continue  # the caption is already part of the table's own text
        if used + len(text) > max_chars:
            continue  # a shorter paragraph further down may still fit
        chosen.append(text)
        used += len(text)
    return "\n\n".join(chosen)


def _label_of(blocks: Sequence[ContentBlock], index: int) -> str | None:
    """Return the table's "Table N", even when its caption is not part of the table block.

    In order: the table's own caption, the caption of the table it continues, then a
    "Table N ..." block just above it, which Docling sometimes leaves unlinked or labels a
    section header.
    """
    own = table_label(blocks[index].text)
    if own is not None:
        return own
    continued = _continued_table(blocks, index)
    if continued:
        return table_label(continued)
    before = _block_before(blocks, index)
    return table_label(before.text) if before is not None and before.kind == "text" else None


def _block_before(blocks: Sequence[ContentBlock], index: int) -> ContentBlock | None:
    """Return the block above blocks[index], skipping page headers and footers."""
    for block in reversed(blocks[:index]):
        if block.label not in _FURNITURE_LABELS:
            return block
    return None


def _section_of(blocks: Sequence[ContentBlock], index: int) -> str:
    """Return the real section heading nearest above the table, or "" when there is none.

    Running headers ("ACCEPTED MANUSCRIPT") and captions labelled as headings are skipped,
    as the search index skips them.
    """
    for block in reversed(blocks[:index]):
        if block.label == "section_header" and is_section_heading(block.text):
            return " ".join(block.text.split())[:MAX_SECTION_CHARS]
    return ""


def _continued_table(blocks: Sequence[ContentBlock], index: int) -> str:
    """Return the caption and header of the table that the uncaptioned table at index continues.

    An uncaptioned table that follows a table of the same width, with only page furniture,
    captions and notes between them, is that table carried onto the next page. A table split
    over several pages is followed back to the part that has the caption.
    """
    table = blocks[index].text
    rows = table_rows(table)
    if table_caption(table) or not rows:
        return ""
    for position in range(index - 1, -1, -1):
        block = blocks[position]
        if block.kind == "table":
            previous = table_rows(block.text)
            if not previous or len(previous[0]) != len(rows[0]):
                return ""
            caption = table_caption(block.text)
            if not caption:
                return _continued_table(blocks, position)
            return f"{caption}\n| {' | '.join(previous[0])} |"
        if block.label not in _BETWEEN_PAGES_LABELS:
            return ""
    return ""


def _prose(block: ContentBlock) -> str | None:
    text = " ".join(block.text.split())
    return text if block.kind == "text" and block.label in _PROSE_LABELS and text else None


def _notes_after(blocks: Sequence[ContentBlock], index: int) -> list[str]:
    notes: list[str] = []
    for block in blocks[index + 1 :]:
        if block.label == "caption":
            continue  # Docling repeats the table's own caption after it
        if block.label not in _NOTE_LABELS or (text := _prose(block)) is None:
            break
        notes.append(text)
    return notes


def _paragraph_before(blocks: Sequence[ContentBlock], index: int) -> list[str]:
    block = _block_before(blocks, index)
    if block is None or block.kind != "text":
        return []
    text = " ".join(block.text.split())
    # A "Table N ..." caption still explains the table when Docling labelled it a heading
    is_explaining = block.label in _PROSE_LABELS or table_label(text) is not None
    return [text] if text and is_explaining else []


def _citing_paragraphs(blocks: Sequence[ContentBlock], label: str | None) -> list[str]:
    if label is None:
        return []
    number = re.escape(label.removeprefix("Table "))
    # "Table 3" or "Tab. 3.", but not "Table 31" or "Table 3.2"
    citation = re.compile(rf"\bTab(?:le|\.)?s?\s*{number}(?!\w|\.\d)", re.IGNORECASE)
    return [text for block in blocks if (text := _prose(block)) and citation.search(text)]


def _defining_paragraphs(blocks: Sequence[ContentBlock], terms: Sequence[str]) -> list[str]:
    if not terms:
        return []
    alternatives = "|".join(re.escape(term) for term in terms)
    # "steel slag powder (SSD)" or "ψ is the ...", "ψ = ...", "ψ denotes ..."
    definition = re.compile(
        rf"\(\s*(?:{alternatives})\s*\)|(?<!\w)(?:{alternatives})\s*(?:=|is\b|denotes\b|"
        r"represents\b|refers to\b)"
    )
    return [text for block in blocks if (text := _prose(block)) and definition.search(text)]


def _table_terms(table: str) -> list[str]:
    """Return the short codes and symbols in the header row and the first column."""
    rows = table_rows(table)
    if not rows:
        return []
    first_column = [row[0] for row in rows[1:] if row]
    # "ψ (%)" and "UCS (MPa)" are defined in the text as "ψ" and "UCS"
    bare = [_UNIT.sub("", cell).strip() for cell in [*rows[0], *first_column]]
    terms = dict.fromkeys(
        term
        for term in bare
        if 0 < len(term) <= _MAX_TERM_CHARS
        and _CODE.search(term)
        and not _ASCII_WORD.fullmatch(term)
    )
    return list(terms)
