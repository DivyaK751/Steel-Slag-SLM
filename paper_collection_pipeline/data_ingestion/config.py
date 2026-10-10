"""Shared configuration for the steel-slag ingestion pipeline."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
REPOSITORY_DIR = PROJECT_DIR.parents[1]
load_dotenv(REPOSITORY_DIR / ".env")
OUTPUT_DIR = PROJECT_DIR / "outputs"
PDF_DIR = OUTPUT_DIR / "pdfs"

# Legacy Google Shared Drive destination: Documents/Extracted Research Papers.
# Upload remains an explicit optional step; local runs do not require Drive access.
DRIVE_FOLDER_ID = os.getenv("STEEL_SLAG_DRIVE_FOLDER_ID") or None

# Stage 1: OpenAlex retrieval
POSITIVE_SEARCH_TERMS = (
    "steel slag",
    "steelmaking slag",
    "BOF slag",
    "basic oxygen furnace slag",
    "EAF slag",
    "electric arc furnace slag",
    "converter slag",
    "LD slag",
    "ladle slag",
    "ladle furnace slag",
    "stainless steel slag",
    "steel furnace slag",
)

BINDER_CONTEXT_TERMS = (
    "cement",
    "concrete",
    "mortar",
    "binder",
    "cementitious",
    "hydration",
    "hydraulic",
    "alkali activated",
    "alkali-activated",
    "supplementary cementitious",
)

# Never exclude the bare word "furnace": it would remove EAF and BOF papers.
EXCLUSION_TERMS = (
    "blast furnace",
    "GGBFS",
    "GGBS",
    "ground granulated",
)

LANGUAGE_FILTER = "en"
YEAR_FLOOR: int | None = None
OPENALEX_PER_PAGE = 100
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY") or None
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO") or None

SELECT_FIELDS = (
    "id",
    "doi",
    "title",
    "publication_year",
    "type",
    "language",
    "authorships",
    "primary_location",
    "open_access",
    "abstract_inverted_index",
    "topics",
    "cited_by_count",
)

# Delay between requests: approximately 1 request/second without a key,
# or 10 requests/second with a key.
OPENALEX_REQUEST_DELAY = 0.1 if OPENALEX_API_KEY else 1.0
REQUEST_TIMEOUT = 30
REQUEST_MAX_RETRIES = 5

# Stage 3: set the model when selecting an LLM provider.
LLM_MODEL = os.getenv("LLM_MODEL") or None
LLM_TEMPERATURE = 0.0
CONFIDENCE_THRESHOLD: str | None = None
CACHE_LLM_RESULTS = True

# Stage 4: PDF downloads
PDF_DOWNLOAD_TIMEOUT = 30
VERIFY_PDF_MAGIC_BYTES = True