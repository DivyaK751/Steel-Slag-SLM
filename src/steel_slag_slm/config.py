"""Load configs/base.yaml and .env into one typed settings object."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
BASE_CONFIG_PATH = CONFIGS_DIR / "base.yaml"


class FetchSettings(BaseModel):
    queries: list[str]
    search_filter: str
    sort: str
    per_page: int
    max_pages_per_query: int
    request_timeout_s: float
    min_budget_usd: float


class RelevanceRules(BaseModel):
    slag_terms: list[str]
    include_terms: list[str]
    include_title_terms: list[str]
    exclude_title_terms: list[str]


class DriveSettings(BaseModel):
    rejected_subfolder_name: str


class Settings(BaseModel):
    fetch: FetchSettings
    relevance: RelevanceRules
    drive: DriveSettings
    openalex_api_key: SecretStr
    contact_email: str
    drive_folder_id: str


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set (see .env.example)")
    return value


def load_settings(config_path: Path = BASE_CONFIG_PATH) -> Settings:
    """Build settings from the YAML config plus secrets from the environment or .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    raw_config = yaml.safe_load(config_path.read_text())
    return Settings(
        **raw_config,
        openalex_api_key=SecretStr(_require_env("OPENALEX_API_KEY")),
        contact_email=_require_env("OPENALEX_CONTACT_EMAIL"),
        drive_folder_id=_require_env("DRIVE_FOLDER_ID"),
    )
