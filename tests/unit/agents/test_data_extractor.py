import re

import pytest

from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    KEY_HEADERS,
    LITERATURE_TABLE,
    PROVENANCE_TABLE,
    SHEET_TABLES,
    column_unit,
)
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt

# Every extraction prompt version must describe the same columns, units and allowed values
PROMPTS = [load_prompt(name) for name in ("extract_data", "extract_data_v3", "extract_data_v4")]
FILLED_BY_FACTS = [
    (sheet_table.table, header)
    for sheet_table in SHEET_TABLES
    if sheet_table.table not in (LITERATURE_TABLE, PROVENANCE_TABLE)
    for header in sheet_table.headers
    if header not in KEY_HEADERS
]


def _field_entry(prompt: PromptTemplate, sheet: str, header: str) -> str:
    """The prompt's text for one field under its sheet, with line wraps joined."""
    sheet_text = prompt.instruction.split(f"\n{sheet}\n", 1)[1].split("\n\n", 1)[0]
    match = re.search(rf"^- {re.escape(header)}\b(.*(?:\n  .*)*)", sheet_text, re.MULTILINE)
    assert match, f"{sheet}.{header} is not described in the prompt"
    return " ".join(match.group(1).split())


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: prompt.name)
@pytest.mark.parametrize(("sheet", "header"), FILLED_BY_FACTS)
def test_prompt_describes_every_column_with_its_unit_and_allowed_values(
    prompt: PromptTemplate, sheet: str, header: str
) -> None:
    entry = _field_entry(prompt, sheet, header)
    unit = column_unit(header)
    if unit:
        assert entry.startswith(f"[{unit}]"), f"{header}: unit should be [{unit}]"
    allowed = ALLOWED_VALUES.get(header)
    if allowed:
        assert entry.startswith(f"[one of: {', '.join(allowed)}]"), header


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: prompt.name)
def test_extract_data_prompt_renders_the_paper_markdown(prompt: PromptTemplate) -> None:
    message = prompt.render_input(openalex_id="W1", title="T", markdown="| a | {b} |")
    assert "| a | {b} |" in message
