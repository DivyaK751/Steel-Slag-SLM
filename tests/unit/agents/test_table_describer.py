import asyncio
from typing import Any

import pytest

from steel_slag_slm.agents import table_describer
from steel_slag_slm.agents.runner import StructuredAnswer
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.schemas import (
    DescribedColumn,
    TableDescription,
    TableTerm,
    TableToDescribe,
    TokenUsage,
)

PROMPT = PromptTemplate(
    "p",
    1,
    "d",
    "instruction",
    "{paper_id}|{paper_title}|{section}|{table_label}|{table}|{previous_table}|{context}"
    "|{correction}",
    (
        "paper_id", "paper_title", "section", "table_label", "table", "previous_table",
        "context", "correction",
    ),
)  # fmt: skip
TABLE = TableToDescribe(
    paper_id="W1",
    block_index=4,
    label="Table 2",
    table="Table 2. CO2 uptake\n\n| Binder | ψ (%) |\n| SSD | 12.1 |",
    context="ψ is the CO2 mineralization potential after 28 d.",
    table_sha256="0" * 64,
    paper_title="CO2 uptake of slag binders",
    section="3. Results",
)
USAGE = TokenUsage(input_tokens=200, output_tokens=40)
INVENTED = TableDescription(summary="SSD reaches 15.3% after 28 d.")
GROUNDED = TableDescription(summary="ψ of SSD after 28 d.")


def _answer_with(
    monkeypatch: pytest.MonkeyPatch, *answers: TableDescription | Exception
) -> list[str]:
    """Answer each call with the next of answers (the last repeats); return the messages sent."""
    sent: list[str] = []

    async def fake_run(_runner: object, message: str, _schema: object) -> Any:
        sent.append(message)
        answer = answers[min(len(sent), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return StructuredAnswer(answer, USAGE)

    monkeypatch.setattr(table_describer, "run_structured_with_usage", fake_run)
    return sent


def _describe(table: TableToDescribe = TABLE, timeout_s: float = 5) -> Any:
    return asyncio.run(
        table_describer.describe_table(object(), PROMPT, table, timeout_s)  # type: ignore[arg-type]
    )


def test_describe_table_sends_the_table_and_its_context(monkeypatch: pytest.MonkeyPatch) -> None:
    description = TableDescription(
        summary="Table 2: ψ, the CO2 mineralization potential of SSD after 28 d.",
        terms=[TableTerm(term="ψ", meaning="CO2 mineralization potential")],
    )
    sent = _answer_with(monkeypatch, description)

    described = _describe()

    assert sent == [
        f"W1|CO2 uptake of slag binders|3. Results|Table 2|{TABLE.table}|(none)|{TABLE.context}"
        "|(none)"
    ]
    assert described == table_describer.DescribedTable(description, USAGE)


def test_describe_table_marks_a_missing_label_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _answer_with(monkeypatch, TableDescription(summary="Oxides of SSD."))

    _describe(TableToDescribe("W1", 4, None, "| Oxide | SSD |", "", "0" * 64))

    assert sent == ["W1|(none)|(none)|(none)|| Oxide | SSD ||(none)|(none)|(none)"]


def test_describe_table_strips_control_characters_copied_from_the_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _answer_with(monkeypatch, GROUNDED)

    _describe(TableToDescribe("W1", 4, None, "| 100 \x06 2 °C |", "", "0" * 64))

    assert "| 100  2 °C |" in sent[0] and "\x06" not in sent[0]


def test_describe_table_asks_again_naming_the_invented_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _answer_with(monkeypatch, INVENTED, GROUNDED)

    described = _describe()

    assert described == table_describer.DescribedTable(GROUNDED, USAGE + USAGE)  # both billed
    assert [message.rsplit("|", 1)[1] for message in sent] == ["(none)", "15.3"]


PAPER_WORDS = TableDescription(
    summary="Table 2. CO2 uptake",
    columns=[DescribedColumn(label="Binder"), DescribedColumn(label="ψ (%)")],
)


def test_describe_table_drops_an_answer_still_inventing_after_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _answer_with(monkeypatch, INVENTED)

    described = _describe()

    assert len(sent) == table_describer.MAX_ATTEMPTS
    assert described == table_describer.DescribedTable(
        PAPER_WORDS, USAGE + USAGE, "paper", "states numbers its inputs never print: 15.3"
    )


def test_describe_table_asks_again_after_a_malformed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_with(monkeypatch, AgentOutputError("Invalid JSON", usage=USAGE), GROUNDED)

    assert _describe() == table_describer.DescribedTable(GROUNDED, USAGE + USAGE)


def test_describe_table_uses_the_papers_words_when_no_answer_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_with(monkeypatch, AgentOutputError("Invalid JSON", usage=USAGE))

    described = _describe()

    assert (described.description, described.source) == (PAPER_WORDS, "paper")
    assert described.problem == "Invalid JSON"
    assert described.usage == USAGE + USAGE


def test_describe_table_gives_each_attempt_its_own_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def too_slow(_runner: object, _message: str, _schema: object) -> Any:
        await asyncio.sleep(1)

    monkeypatch.setattr(table_describer, "run_structured_with_usage", too_slow)

    described = _describe(timeout_s=0.01)

    assert (described.description, described.source) == (PAPER_WORDS, "paper")
    assert described.problem == "no answer within 0.01 s"


def test_describe_table_raises_when_gemini_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_with(monkeypatch, RuntimeError("429 RESOURCE_EXHAUSTED"))

    with pytest.raises(RuntimeError, match="429"):
        _describe()


def test_describe_table_allows_numbers_printed_in_the_title_or_previous_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = "Continuation of Table 7 for the 2021 study."
    _answer_with(monkeypatch, TableDescription(summary=summary))
    continued = TableToDescribe(
        "W1", 9, None, "| SSD | 12.1 |", "", "0" * 64, "Slag binders in 2021", "",
        "Table 7. CO2 uptake\n| Binder | ψ (%) |",
    )  # fmt: skip

    assert _describe(continued).source == "gemini"


def test_ungrounded_numbers_ignores_formula_digits_and_decimal_commas() -> None:
    description = TableDescription(summary="Al2O3 and CO2 of slag 9#, 10,6 MPa at 28 d.")

    assert table_describer.ungrounded_numbers(description, "| 9# | 10.6 | 28 d |") == []


def test_ungrounded_numbers_counts_digits_printed_glued_to_text() -> None:
    description = TableDescription(summary="Table 2: strength at 14 and 28 d, strains 4 to 8.")
    source = "Table2. Strength | strength14d | p28d | ε4 | ε8 | 28.79"

    assert table_describer.ungrounded_numbers(description, source) == []


def test_paper_description_adds_no_words_of_its_own() -> None:
    bare = TableToDescribe("W1", 7, None, "| Mix |  | w/b |\n| M1 | 3 | 0.4 |", "", "0" * 64)
    labelled = TableToDescribe("W1", 7, "Table 3", "| Mix |\n| M1 |", "", "0" * 64)

    assert table_describer.paper_description(bare) == TableDescription(
        summary="", columns=[DescribedColumn(label="Mix"), DescribedColumn(label="w/b")]
    )
    assert table_describer.paper_description(labelled).summary == "Table 3"


def test_table_name_names_uncaptioned_tables_by_block() -> None:
    bare = TableToDescribe("W1", 7, None, "| a |", "", "0" * 64)

    assert table_describer.table_name(bare) == "uncaptioned table (block 7)"
    assert table_describer.table_name(TABLE) == "Table 2"


def test_describe_table_prompt_loads() -> None:
    prompt = load_prompt("describe_table")

    assert set(prompt.variables) == {
        "paper_id", "paper_title", "section", "table_label", "table", "previous_table",
        "context", "correction",
    }  # fmt: skip
    assert "<previous_table>" in prompt.input_template
