"""Prompt construction and output validation for relevance classification."""

import json
from typing import Any


DECISIONS = {"Yes", "No", "Unclear"}
CONFIDENCES = {"High", "Medium", "Low"}
APPLICATIONS = {
    "binder/SCM",
    "aggregate",
    "asphalt",
    "soil",
    "characterization",
    "carbonation",
    "other",
}
SLAG_TYPES = {
    "EAF",
    "BOF",
    "Ladle",
    "Stainless Steel",
    "Other Steel Slag",
    "Not specified",
}


def build_prompt(title: str, abstract: str) -> str:
    """Build a concise prompt for deciding whether a paper is relevant."""
    return f"""Classify this paper for a steel-slag cementitious-materials literature collection.

Relevant papers study steel slag in cementitious applications, hydration, hydraulic binders, carbonation, or chemical, mineralogical, or morphological characterization. The target material is steelmaking slag (such as EAF, BOF, ladle, or stainless-steel slag), not blast-furnace slag (GGBFS/GGBS).

Decide whether the paper studies steel slag as a reactive binder or supplementary cementitious material (SCM), rather than only as inert aggregate, in asphalt, for soil stabilization, or in another unrelated application. For dual-use papers, answer Yes when binder use is primary and binder-relevant data are reported; answer No when aggregate use is primary; answer Unclear when the uses are genuinely co-equal or the abstract does not provide enough evidence.

Do not infer details that the title and abstract do not state. Use Unclear when evidence is insufficient. The supporting excerpt must be copied verbatim from the abstract. If the abstract does not support a decision, use an empty excerpt.

Return only a JSON object with these keys:
{{
  "decision": "Yes | No | Unclear",
  "confidence": "High | Medium | Low",
  "primary_application": "binder/SCM | aggregate | asphalt | soil | characterization | carbonation | other",
  "slag_type": "EAF | BOF | Ladle | Stainless Steel | Other Steel Slag | Not specified",
  "rationale": "One sentence explaining the decision.",
  "supporting_excerpt": "Verbatim quote from the abstract, or an empty string."
}}

Title: {title}

Abstract:
{abstract}
"""


def parse_classification(raw_output: str, abstract: str) -> dict[str, Any]:
    """Parse and validate one JSON classification returned by an LLM."""
    try:
        result = json.loads(raw_output)
    except json.JSONDecodeError as error:
        raise ValueError("Classifier output must be valid JSON.") from error

    if not isinstance(result, dict):
        raise ValueError("Classifier output must be a JSON object.")

    required = {
        "decision",
        "confidence",
        "primary_application",
        "slag_type",
        "rationale",
        "supporting_excerpt",
    }
    missing = required - result.keys()
    if missing:
        raise ValueError(f"Classifier output is missing keys: {sorted(missing)}")

    if result["decision"] not in DECISIONS:
        raise ValueError("Invalid decision.")
    if result["confidence"] not in CONFIDENCES:
        raise ValueError("Invalid confidence.")
    if result["primary_application"] not in APPLICATIONS:
        raise ValueError("Invalid primary_application.")
    if result["slag_type"] not in SLAG_TYPES:
        raise ValueError("Invalid slag_type.")

    for key in ("rationale", "supporting_excerpt"):
        if not isinstance(result[key], str):
            raise ValueError(f"{key} must be a string.")

    excerpt = result["supporting_excerpt"]
    if excerpt and excerpt not in abstract:
        raise ValueError("Supporting excerpt must appear verbatim in the abstract.")

    return {key: result[key] for key in required}