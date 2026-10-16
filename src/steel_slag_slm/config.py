"""Load configs/base.yaml and .env into one typed settings object."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
BASE_CONFIG_PATH = CONFIGS_DIR / "base.yaml"
# Kept outside the repo so a personal Google login can never be committed
DEFAULT_GOOGLE_TOKEN_PATH = Path.home() / ".config" / "steel_slag_slm" / "google_token.json"


class FetchSettings(BaseModel):
    """How to search OpenAlex and when to stop spending the daily budget."""

    queries: list[str]
    search_filter: str
    sort: str
    per_page: int
    max_pages_per_query: int
    request_timeout_s: float
    min_budget_usd: float


class RelevanceRules(BaseModel):
    """Term lists that decide which papers are worth extracting."""

    slag_terms: list[str]
    include_terms: list[str]
    include_title_terms: list[str]
    exclude_title_terms: list[str]
    other_slag_title_terms: list[str]


class DriveSettings(BaseModel):
    """Layout of the shared Drive folder."""

    rejected_subfolder_name: str
    no_pdf_list_name: str


class RegistrySettings(BaseModel):
    """Where the BigQuery table listing every paper in the Drive folder lives."""

    dataset: str
    table: str
    location: str


class Settings(BaseModel):
    """Everything the pipelines need, from configs/ and the environment."""

    fetch: FetchSettings
    relevance: RelevanceRules
    drive: DriveSettings
    registry: RegistrySettings
    openalex_api_key: SecretStr
    contact_email: str
    drive_folder_id: str
    # Exactly one Google sign-in is used: a service account (CI) or a personal login (local)
    google_service_account_file: Path | None = None
    google_oauth_client_file: Path | None = None
    google_oauth_token_file: Path = DEFAULT_GOOGLE_TOKEN_PATH
    # BigQuery is optional: without a project the fetcher skips the registry
    gcp_project: str | None = None
    # Separate from the Drive sign-in, which may be a personal login without BigQuery access
    gcp_credentials_file: Path | None = None


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set (see .env.example)")
    return value


def _optional_path_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        raise RuntimeError(f"{name} points to {path}, which does not exist (see README)")
    return path


def load_settings(config_path: Path = BASE_CONFIG_PATH) -> Settings:
    """Build settings from the YAML config plus secrets from the environment or .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    raw_config = yaml.safe_load(config_path.read_text())
    return Settings(
        **raw_config,
        openalex_api_key=SecretStr(_require_env("OPENALEX_API_KEY")),
        contact_email=_require_env("OPENALEX_CONTACT_EMAIL"),
        drive_folder_id=_require_env("DRIVE_FOLDER_ID"),
        google_service_account_file=_optional_path_env("GOOGLE_APPLICATION_CREDENTIALS"),
        google_oauth_client_file=_optional_path_env("GOOGLE_OAUTH_CLIENT_FILE"),
        # Unlike the paths above this may not exist yet: the first sign-in creates it
        google_oauth_token_file=Path(
            os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip() or DEFAULT_GOOGLE_TOKEN_PATH
        ).expanduser(),
        gcp_project=os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip() or None,
        gcp_credentials_file=_optional_path_env("GCP_CREDENTIALS_FILE"),
    )
