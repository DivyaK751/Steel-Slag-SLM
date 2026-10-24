"""Load configs/base.yaml and .env into one typed settings object."""

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
BASE_CONFIG_PATH = CONFIGS_DIR / "base.yaml"
RETRIEVAL_EVAL_PATH = CONFIGS_DIR / "retrieval_eval.yaml"
# Kept outside the repo so a personal Google login can never be committed
DEFAULT_GOOGLE_TOKEN_PATH = Path.home() / ".config" / "steel_slag_slm" / "google_token.json"


class FetchSettings(BaseModel):
    """How to search OpenAlex and when to stop spending the daily budget."""

    queries: list[str]
    search_filter: str
    sort: str
    per_page: int
    pages_per_discovery: int
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
    queue_list_name: str
    search_progress_name: str
    no_new_papers_name: str


class DriveConnectionSettings(BaseModel):
    """Credentials and source folder used to connect to Google Drive."""

    folder_id: str
    service_account_file: Path | None = None
    oauth_client_file: Path | None = None
    oauth_token_file: Path = DEFAULT_GOOGLE_TOKEN_PATH


class RegistrySettings(BaseModel):
    """Where the BigQuery table listing every paper in the Drive folder lives."""

    dataset: str
    table: str
    location: str


class PdfParsingSettings(BaseModel):
    """Where PDF parsing reads paper rows and stores its Drive outputs."""

    source_table: str
    output_folder_name: str
    index_file_name: str


class EmbeddingSettings(BaseModel):
    """How parsed papers are chunked, embedded and stored in Drive."""

    model: str
    query_prefix: str
    chunk_max_chars: int
    table_max_chars: int
    papers_per_save: int
    top_k: int


class EvidenceSpan(BaseModel):
    """A passage that answers a test question, identified by text copied from the paper."""

    paper_id: str
    kind: Literal["text", "table", "figure_caption"]
    text: str  # matched inside chunk text, ignoring case and spacing
    grade: Literal[1, 2] = 2  # 2 answers the question directly, 1 answers it in part


class EvalQuestion(BaseModel):
    """One fixed test question and every passage known to answer it."""

    id: str
    query: str
    evidence: list[EvidenceSpan] = Field(min_length=1)


class RetrievalEvalSet(BaseModel):
    """The fixed set of questions used to score retrieval."""

    depth: int  # how many results to retrieve per question; the largest K that is reported
    questions: list[EvalQuestion]
    paper_id: str | None = None  # when set, search only this paper's chunks


class GcpSettings(BaseModel):
    """The Google Cloud project and region that Vertex AI and BigQuery calls are billed to."""

    project: str
    location: str
    # Unattended runs only; locally BigQuery uses `gcloud auth application-default login`
    credentials_file: Path | None = None


class AgentSettings(BaseModel):
    """Model settings shared by every agent: the `llm:` defaults plus the agent's overrides."""

    model: str
    temperature: float


class ScreeningSettings(AgentSettings):
    """How the relevance-screening agent reads and fills the BigQuery papers table."""

    prompt_name: str
    batch_size: int
    max_concurrency: int
    papers_table: str  # fully qualified: project.dataset.table


class AbstractExtractionSettings(AgentSettings):
    """How the abstract-extraction agent fills missing abstracts from a paper's first pages."""

    prompt_name: str
    max_pages: int
    batch_size: int
    max_concurrency: int
    call_timeout_s: float
    papers_table: str  # fully qualified: project.dataset.table


class EvaluationSettings(BaseModel):
    """Where the human-labelled papers live and where run reports are saved."""

    labels_table: str  # fully qualified: project.dataset.table
    output_dir: Path


class Settings(BaseModel):
    """Everything the paper fetcher needs, from configs/ and the environment."""

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


def _read_base_config(config_path: Path) -> dict[str, Any]:
    """Load .env into the environment and return the parsed YAML config."""
    load_dotenv(PROJECT_ROOT / ".env")
    raw_config: dict[str, Any] = yaml.safe_load(config_path.read_text())
    return raw_config


def _agent_section(raw_config: dict[str, Any], name: str) -> dict[str, Any]:
    """Merge the shared `llm:` defaults with one agent's section; the agent's values win."""
    overrides = {key: value for key, value in raw_config[name].items() if value is not None}
    return {**raw_config["llm"], **overrides}


def _papers_table(raw_config: dict[str, Any]) -> str:
    """The fully qualified papers table that the agents read from and write to."""
    registry = RegistrySettings(**raw_config["registry"])
    # The override lets Gemini bill one project while the papers table lives in another
    return os.environ.get("BIGQUERY_PAPERS_TABLE", "").strip() or (
        f"{_require_env('GOOGLE_CLOUD_PROJECT')}.{registry.dataset}.{registry.table}"
    )


def load_settings(config_path: Path = BASE_CONFIG_PATH) -> Settings:
    """Build settings from the YAML config plus secrets from the environment or .env."""
    raw_config = _read_base_config(config_path)
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


def load_gcp_settings() -> GcpSettings:
    """Read the GCP project and region from the environment or .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    # ADK reads this flag itself; this project always calls Gemini through Vertex AI
    os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "TRUE")
    return GcpSettings(
        project=_require_env("GOOGLE_CLOUD_PROJECT"),
        location=_require_env("GOOGLE_CLOUD_LOCATION"),
        credentials_file=_optional_path_env("GCP_CREDENTIALS_FILE"),
    )


def load_screening_settings(config_path: Path = BASE_CONFIG_PATH) -> ScreeningSettings:
    """Build the screening agent's settings; needs no OpenAlex or Drive secrets."""
    raw_config = _read_base_config(config_path)
    return ScreeningSettings(
        **_agent_section(raw_config, "screening"), papers_table=_papers_table(raw_config)
    )


def load_abstract_extraction_settings(
    config_path: Path = BASE_CONFIG_PATH,
) -> AbstractExtractionSettings:
    """Build the abstract-extraction agent's settings; needs no OpenAlex secrets."""
    raw_config = _read_base_config(config_path)
    return AbstractExtractionSettings(
        **_agent_section(raw_config, "abstract_extraction"),
        papers_table=_papers_table(raw_config),
    )


def load_evaluation_settings(
    papers_table: str, config_path: Path = BASE_CONFIG_PATH
) -> EvaluationSettings:
    """Build evaluation settings; the labels table sits next to the papers table."""
    raw_config = _read_base_config(config_path)["evaluation"]
    project_and_dataset = papers_table.rsplit(".", 1)[0]
    return EvaluationSettings(
        labels_table=f"{project_and_dataset}.{raw_config['table']}",
        output_dir=PROJECT_ROOT / raw_config["output_dir"],
    )


def load_pdf_parsing_settings(
    config_path: Path = BASE_CONFIG_PATH,
) -> PdfParsingSettings:
    """Build PDF-parsing settings from the YAML config."""
    raw_config = _read_base_config(config_path)
    return PdfParsingSettings(**raw_config["pdf_parsing"])


def load_drive_connection_settings() -> DriveConnectionSettings:
    """Load Drive authentication and source-folder settings from the environment."""
    load_dotenv(PROJECT_ROOT / ".env")
    return DriveConnectionSettings(
        folder_id=_require_env("DRIVE_FOLDER_ID"),
        service_account_file=_optional_path_env("GOOGLE_APPLICATION_CREDENTIALS"),
        oauth_client_file=_optional_path_env("GOOGLE_OAUTH_CLIENT_FILE"),
        oauth_token_file=Path(
            os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip() or DEFAULT_GOOGLE_TOKEN_PATH
        ).expanduser(),
    )


def load_embedding_settings(config_path: Path = BASE_CONFIG_PATH) -> EmbeddingSettings:
    """Build chunking and embedding settings from the YAML config."""
    raw_config = _read_base_config(config_path)
    return EmbeddingSettings(**raw_config["embedding"])


def load_retrieval_eval_set(path: Path = RETRIEVAL_EVAL_PATH) -> RetrievalEvalSet:
    """Read the fixed retrieval test set from configs/retrieval_eval.yaml."""
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RetrievalEvalSet(**raw)
