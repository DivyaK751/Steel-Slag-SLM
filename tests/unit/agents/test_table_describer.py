import asyncio
from typing import Any

import pytest

from steel_slag_slm.agents import table_describer
from steel_slag_slm.agents.runner import StructuredAnswer
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.schemas import TableDescription, TableTerm, TableToDescribe, TokenUsage

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


def _answer_with(
    description: TableDescription, monkeypatch: pytest.MonkeyPatch, *later: TableDescription
) -> list[str]:
    """Answer with description, then with each of later in turn; return the messages sent."""
    sent: list[str] = []
    answers = [description, *later]

    async def fake_run(_runner: object, message: str, _schema: object) -> Any:
        sent.append(message)
        return StructuredAnswer(answers[min(len(sent), len(answers)) - 1], USAGE)

    monkeypatch.setattr(table_describer, "run_structured_with_usage", fake_run)
    return sent


def test_describe_table_sends_the_table_and_its_context(monkeypatch: pytest.MonkeyPatch) -> None:
    description = TableDescription(
        summary="Table 2: ψ, the CO2 mineralization potential of SSD after 28 d.",
        terms=[TableTerm(term="ψ", meaning="CO2 mineralization potential")],
    )
    sent = _answer_with(description, monkeypatch)

    result, usage = asyncio.run(
        table_describer.describe_table(object(), PROMPT, TABLE)  # type: ignore[arg-type]
    )

    assert sent == [
        f"W1|CO2 uptake of slag binders|3. Results|Table 2|{TABLE.table}|(none)|{TABLE.context}"
        "|(none)"
    ]
    assert (result, usage) == (description, USAGE)


def test_describe_table_marks_a_missing_label_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _answer_with(TableDescription(summary="Oxides of SSD."), monkeypatch)
    bare = TableToDescribe("W1", 4, None, "| Oxide | SSD |", "", "0" * 64)

    asyncio.run(table_describer.describe_table(object(), PROMPT, bare))  # type: ignore[arg-type]

    assert sent == ["W1|(none)|(none)|(none)|| Oxide | SSD ||(none)|(none)|(none)"]


INVENTED = TableDescription(summary="SSD reaches 15.3% after 28 d.")


def test_describe_table_asks_again_naming_the_invented_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grounded = TableDescription(summary="ψ of SSD after 28 d.")
    sent = _answer_with(INVENTED, monkeypatch, grounded)

    result, usage = asyncio.run(
        table_describer.describe_table(object(), PROMPT, TABLE)  # type: ignore[arg-type]
    )

    assert result == grounded
    assert [message.rsplit("|", 1)[1] for message in sent] == ["(none)", "15.3"]
    assert usage == USAGE + USAGE  # both calls are billed


def test_describe_table_rejects_numbers_still_invented_after_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _answer_with(INVENTED, monkeypatch)

    with pytest.raises(AgentOutputError, match="15.3") as error:
        asyncio.run(
            table_describer.describe_table(object(), PROMPT, TABLE)  # type: ignore[arg-type]
        )
    assert len(sent) == table_describer.MAX_ATTEMPTS
    assert error.value.usage == USAGE + USAGE  # the failed calls were still billed


def test_describe_table_allows_numbers_printed_in_the_title_or_previous_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_with(
        TableDescription(summary="Continuation of Table 7 for the 2021 study."), monkeypatch
    )
    continued = TableToDescribe(
        "W1", 9, None, "| SSD | 12.1 |", "", "0" * 64, "Slag binders in 2021", "",
        "Table 7. CO2 uptake\n| Binder | ψ (%) |",
    )  # fmt: skip

    asyncio.run(table_describer.describe_table(object(), PROMPT, continued))  # type: ignore[arg-type]


def test_ungrounded_numbers_ignores_formula_digits_and_decimal_commas() -> None:
    description = TableDescription(summary="Al2O3 and CO2 of slag 9#, 10,6 MPa at 28 d.")

    assert table_describer.ungrounded_numbers(description, "| 9# | 10.6 | 28 d |") == []


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
