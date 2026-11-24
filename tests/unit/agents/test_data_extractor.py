import asyncio
import re
from typing import Any

import pytest

from steel_slag_slm.agents import data_extractor
from steel_slag_slm.agents.data_extractor import NO_FIGURES, format_figures, merge_parts
from steel_slag_slm.agents.runner import StructuredAnswer
from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    KEY_HEADERS,
    LITERATURE_TABLE,
    PROVENANCE_TABLE,
    SHEET_TABLES,
    column_unit,
)
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.schemas import (
    ExtractedFact,
    ExtractedLiterature,
    ExtractedMaterial,
    ExtractedRow,
    ExtractedValue,
    FigureReading,
    PaperExtraction,
    PaperFigure,
    ParsedPaper,
    RowExtraction,
    TableColumn,
    TokenUsage,
)

# Every extraction prompt version must describe the same columns, units and allowed values
PROMPTS = [
    load_prompt(name)
    for name in (
        "extract_data",
        "extract_data_v3",
        "extract_data_v4",
        "extract_data_v9",
    )
]
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
    figures = {"figures": "| x |"} if "figures" in prompt.variables else {}
    message = prompt.render_input(openalex_id="W1", title="T", markdown="| a | {b} |", **figures)
    assert "| a | {b} |" in message


def _figure(
    caption: str = "Figure 4. Strength vs time. Figure 4. Strength vs time.",
) -> PaperFigure:
    reading = FigureReading(
        figure_type="data_plot",
        summary="Strength against carbonation time.",
        columns=[
            TableColumn(label="Carbonation time", unit="hours", source="x_axis"),
            TableColumn(label="Legend", source="legend"),
            TableColumn(label="Compressive strength", unit="MPa", source="y_axis"),
        ],
        rows=[["1", "SSD", "9.3"], ["3", "SSD", "12.0"]],
        phases=["C2S"],
    )
    return PaperFigure(figure_number="4", caption=caption, pages=(7,), reading=reading)


def test_format_figures_renders_each_reading_as_a_markdown_table() -> None:
    text = format_figures([_figure()])
    assert text.splitlines() == [
        "### Figure 4, page 7",
        "Caption: Figure 4. Strength vs time.",  # the parser's doubled caption, once
        "Type: data_plot. Strength against carbonation time.",
        "| Carbonation time (hours) | Legend | Compressive strength (MPa) |",
        "| --- | --- | --- |",
        "| 1 | SSD | 9.3 |",
        "| 3 | SSD | 12.0 |",
        "Phases labelled: C2S",
    ]


def test_format_figures_says_so_when_a_paper_has_no_readings() -> None:
    assert format_figures([]) == NO_FIGURES
    unread = PaperFigure(
        figure_number="2", caption="Figure 2. SEM image.", pages=(3,), reading=None
    )
    assert format_figures([unread]) == NO_FIGURES


def _fact(sheet: Any, material_id: str = "MAT-0001") -> ExtractedFact:
    return ExtractedFact(
        sheet=sheet, field="Notes", value="v", reported_value="v", material_id=material_id,
        experiment_id=None, mix_id=None, location="Table 1", excerpt="v", confidence="High",
    )  # fmt: skip


def _answer(facts: list[ExtractedFact], material_ids: list[str]) -> StructuredAnswer[Any]:
    literature = ExtractedLiterature(
        publication_type=None, journal_or_source=None, country_or_region=None,
        study_objective=None, abstract_or_key_finding=None, full_text_access=None,
        relevant_to_scm="Yes", notes=None,
    )  # fmt: skip
    materials = [ExtractedMaterial(material_id=m, steel_slag_type="BOF", description=m)
                 for m in material_ids]  # fmt: skip
    extraction = PaperExtraction(literature=literature, materials=materials, facts=facts)
    return StructuredAnswer(extraction, TokenUsage(input_tokens=10, output_tokens=5))


def _row_answer() -> StructuredAnswer[Any]:
    literature = _answer([], []).result.literature
    row = ExtractedRow(
        sheet="sheet_06_concrete_performance", material_id="MAT-0001", experiment_id="EXP-0001",
        mix_id="M1", location="Table 4", excerpt="Table 4. Strength of the mixes.",
        confidence="High",
        values=[ExtractedValue(field="SCM_Replacement_pct", value="15"),
                ExtractedValue(field="Compressive_Strength_28d_MPa", value="41.0")],
    )  # fmt: skip
    materials = [ExtractedMaterial(material_id="MAT-0001", steel_slag_type="BOF", description="")]
    extraction = RowExtraction(literature=literature, materials=materials, rows=[row])
    return StructuredAnswer(extraction, TokenUsage(input_tokens=10, output_tokens=5))


def _extract(
    monkeypatch: pytest.MonkeyPatch, replies: list[Any]
) -> tuple[StructuredAnswer[Any], list[tuple[str, object]]]:
    """Extract one paper with prompt v9 from a fake model giving these replies in turn
    (an answer, or an error to raise); return the answer and each (input, schema) asked."""
    calls: list[tuple[str, object]] = []

    async def fake_run(_runner: object, text: str, schema: object) -> StructuredAnswer[Any]:
        calls.append((text, schema))
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply  # type: ignore[no-any-return]

    monkeypatch.setattr(data_extractor, "run_structured_with_usage", fake_run)
    paper = ParsedPaper(openalex_id="W1", markdown="# Paper", tables=())
    answer = asyncio.run(
        data_extractor.extract_paper_data(
            object(), load_prompt("extract_data_v9"), load_prompt("extract_data_part"),  # type: ignore[arg-type]
            paper, "Title",
        )
    )  # fmt: skip
    return answer, calls


def test_a_row_becomes_one_fact_per_value_sharing_its_ids_and_quote() -> None:
    facts = _row_answer().result.to_extraction().facts
    assert [(f.field, f.value) for f in facts] == [
        ("SCM_Replacement_pct", "15"),
        ("Compressive_Strength_28d_MPa", "41.0"),
    ]
    assert {(f.mix_id, f.experiment_id, f.excerpt, f.confidence) for f in facts} == {
        ("M1", "EXP-0001", "Table 4. Strength of the mixes.", "High")
    }


def test_a_paper_is_asked_once_for_rows_and_returned_as_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, calls = _extract(monkeypatch, [_row_answer()])
    assert [schema for _, schema in calls] == [RowExtraction]
    assert len(answer.result.facts) == 2


def test_merge_parts_keeps_each_parts_own_sheets_and_sums_usage() -> None:
    first = _answer(
        [_fact("sheet_02_feedstock_chemistry"), _fact("sheet_07_durability")], ["MAT-0001"]
    )
    second = _answer(
        [_fact("sheet_03_processing"), _fact("sheet_07_durability", "MAT-0002")],
        ["MAT-0001", "MAT-0002"],
    )
    merged = merge_parts([first, second])
    # Each answer's facts for the other part's sheets are dropped
    assert [(f.sheet, f.material_id) for f in merged.result.facts] == [
        ("sheet_02_feedstock_chemistry", "MAT-0001"),
        ("sheet_07_durability", "MAT-0002"),
    ]
    assert [m.material_id for m in merged.result.materials] == ["MAT-0001", "MAT-0002"]
    assert merged.usage == TokenUsage(input_tokens=20, output_tokens=10)


def test_a_cut_off_answer_is_asked_again_in_parts_and_its_tokens_still_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cut = AgentOutputError("cut", '{"rows": [', cut_off=True, usage=TokenUsage(output_tokens=65000))
    answer, calls = _extract(monkeypatch, [cut, _row_answer(), _row_answer()])

    assert len(calls) == 1 + len(data_extractor.EXTRACTION_PARTS)
    first_part, second_part = (text.split("<part>")[1] for text, _ in calls[1:])
    assert "sheet_06_concrete_performance" in first_part and "sheet_07" not in first_part
    # The second part reuses the materials the first part named
    assert "sheet_05_activation" in second_part and "MAT-0001 (BOF)" in second_part
    # Two parts of 5 output tokens each, plus the cut-off call
    assert answer.usage.output_tokens == 65000 + 2 * 5


def test_an_answer_that_is_not_cut_off_is_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AgentOutputError, match="bad json"):
        _extract(monkeypatch, [AgentOutputError("bad json", "{", cut_off=False)])


def test_a_cut_off_part_says_which_part_and_what_its_answer_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = (
        '{"rows": [{"sheet": "sheet_07_durability", "experiment_id": "E1"},'
        ' {"sheet": "sheet_08_carbon_uptake", "experiment_id": "E2"}, {"sheet'
    )
    cut = AgentOutputError("cut (finish MAX_TOKENS)", held, cut_off=True)
    with pytest.raises(AgentOutputError) as raised:
        _extract(monkeypatch, [cut, _row_answer(), cut])
    message = str(raised.value)
    assert message.startswith("Part 2 of 2: cut (finish MAX_TOKENS)")
    assert "2 records (sheet_07 1, sheet_08 1), 2 experiment IDs" in message
