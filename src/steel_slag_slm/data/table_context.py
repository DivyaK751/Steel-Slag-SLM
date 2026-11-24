"""Pick the tables of a parsed paper that need a description, with the text that explains them.

A table's own cells rarely say what its symbols, codes and units mean; the paper says it
elsewhere: in notes under the table, in the sentence that cites it, or where a material code
is first defined ("steel slag powder (SSD)"). Those paragraphs are what the describer reads.
"""

import re
from collections.abc import Sequence

from steel_slag_slm.data.parsed_manifest import ContentBlock, ParsedManifest, table_text_sha256
from steel_slag_slm.data.table_text import (
    compact_markdown_table,
    is_contents_list,
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


def tables_to_describe(
    manifest: ParsedManifest, prompt_version: int, context_max_chars: int
) -> list[TableToDescribe]:
    """Return the paper's tables that lack a description from this prompt version.

    Contents lists that the parser read as tables are left out, as the search index leaves
    them out. Each table carries up to context_max_chars of the paper's explaining text.
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
            label=table_label(block.text),
            table=compact_markdown_table(block.text),
            context=table_context(blocks, index, context_max_chars),
            table_sha256=table_text_sha256(block.text),
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
        *_citing_paragraphs(blocks, table_label(table)),
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
    for block in reversed(blocks[:index]):
        if block.label in ("page_header", "page_footer"):
            continue
        text = _prose(block)
        return [text] if text is not None else []
    return []


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
