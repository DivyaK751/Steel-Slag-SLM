"""Load versioned prompt templates from prompts/<name>.yaml."""

import string
from dataclasses import dataclass
from pathlib import Path

import yaml

from steel_slag_slm.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A fixed instruction plus a per-item input template with named variables."""

    name: str
    version: int
    description: str
    instruction: str
    input_template: str
    variables: tuple[str, ...]

    def render_input(self, **values: object) -> str:
        """Fill the input template; every declared variable must be given, and no others.

        Raises:
            ValueError: If values are missing or not declared by the prompt.
        """
        missing = set(self.variables) - values.keys()
        extra = values.keys() - set(self.variables)
        if missing or extra:
            raise ValueError(
                f"Prompt {self.name}: missing {sorted(missing)}, unexpected {sorted(extra)}"
            )
        return self.input_template.format(**values)


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def load_prompt(name: str, prompts_dir: Path = PROMPTS_DIR) -> PromptTemplate:
    """Read prompts/<name>.yaml and check its placeholders match its declared variables.

    Raises:
        ValueError: If the template uses a variable it doesn't declare, or the reverse.
    """
    raw = yaml.safe_load((prompts_dir / f"{name}.yaml").read_text())
    prompt = PromptTemplate(
        name=raw["name"],
        version=int(raw["version"]),
        description=raw["description"],
        instruction=raw["instruction"],
        input_template=raw["input_template"],
        variables=tuple(raw["variables"]),
    )
    if _placeholders(prompt.input_template) != set(prompt.variables):
        raise ValueError(
            f"Prompt {name}: template placeholders {sorted(_placeholders(prompt.input_template))} "
            f"don't match declared variables {sorted(prompt.variables)}"
        )
    return prompt
