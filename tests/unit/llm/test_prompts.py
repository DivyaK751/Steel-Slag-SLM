from pathlib import Path

import pytest
import yaml

from steel_slag_slm.llm.prompts import load_prompt


def _write_prompt(tmp_path: Path, template: str, variables: list[str]) -> None:
    raw = {
        "name": "demo",
        "version": 2,
        "description": "d",
        "variables": variables,
        "instruction": 'Return {"a": 1}',
        "input_template": template,
    }
    (tmp_path / "demo.yaml").write_text(yaml.safe_dump(raw))


def test_load_prompt_rejects_undeclared_placeholder(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "{title} {abstract}", ["title"])
    with pytest.raises(ValueError, match="don't match"):
        load_prompt("demo", tmp_path)


def test_render_input_fills_template_and_keeps_braces_in_values(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "title: {title}", ["title"])
    prompt = load_prompt("demo", tmp_path)
    assert prompt.version == 2
    assert prompt.render_input(title="CaO {free}") == "title: CaO {free}"


def test_render_input_rejects_missing_or_extra_values(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "{title}", ["title"])
    prompt = load_prompt("demo", tmp_path)
    with pytest.raises(ValueError, match="missing"):
        prompt.render_input(abstract="x")


def test_screen_relevance_prompt_loads() -> None:
    prompt = load_prompt("screen_relevance")
    assert set(prompt.variables) == {"openalex_id", "title", "year", "keywords", "abstract"}
    assert "<paper>" in prompt.input_template
