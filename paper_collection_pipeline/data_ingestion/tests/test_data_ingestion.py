"""Offline tests for ingestion utilities, search construction, and validation."""

import json
import unittest
from types import SimpleNamespace

from paper_collection_pipeline.data_ingestion import config
from paper_collection_pipeline.data_ingestion.classify import (
    build_prompt,
    parse_classification,
)
from paper_collection_pipeline.data_ingestion.io_utils import (
    dedup_records,
    load_csv,
    reconstruct_abstract,
    route_no_abstract,
    save_csv,
)
from paper_collection_pipeline.data_ingestion.openalex_client import (
    _rate_limit_delay,
    build_filter_string,
    build_search_query,
)


class AbstractTests(unittest.TestCase):
    def test_reconstruct_abstract_orders_words(self) -> None:
        index = {"reactive": [2], "slag": [0, 3], "is": [1]}
        self.assertEqual(reconstruct_abstract(index), "slag is reactive slag")

    def test_empty_index_returns_none(self) -> None:
        self.assertIsNone(reconstruct_abstract(None))
        self.assertIsNone(reconstruct_abstract({}))

    def test_route_splits_missing_abstracts(self) -> None:
        records = [
            {
                "id": "W1",
                "abstract_inverted_index": {"Steel": [0], "slag": [1]},
            },
            {"id": "W2", "abstract_inverted_index": None},
        ]

        with_abstract, without_abstract = route_no_abstract(records)

        self.assertEqual(with_abstract[0]["abstract"], "Steel slag")
        self.assertEqual([item["id"] for item in without_abstract], ["W2"])


class DeduplicationTests(unittest.TestCase):
    def test_deduplicates_by_id_and_normalized_doi(self) -> None:
        records = [
            {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.X/ABC"},
            {"id": "https://openalex.org/W1", "doi": None},
            {"id": "https://openalex.org/W2", "doi": "10.x/abc"},
            {"id": "https://openalex.org/W3", "doi": "10.x/unique"},
        ]

        result = dedup_records(records)

        self.assertEqual([item["id"] for item in result], [
            "https://openalex.org/W1",
            "https://openalex.org/W3",
        ])


class CsvTests(unittest.TestCase):
    def test_round_trips_nested_values(self) -> None:
        import tempfile
        from pathlib import Path

        record = {"id": "W1", "topics": [{"name": "materials"}]}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.csv"
            save_csv([record], path)
            loaded = load_csv(path)

        self.assertEqual(loaded, [record])


class OpenAlexQueryTests(unittest.TestCase):
    def test_search_includes_target_terms_and_safe_exclusions(self) -> None:
        query = build_search_query(config)

        self.assertIn('"electric arc furnace slag"', query)
        self.assertIn('"blast furnace"', query)
        self.assertNotIn('NOT ("furnace")', query)

    def test_filter_includes_language(self) -> None:
        settings = SimpleNamespace(LANGUAGE_FILTER="en", YEAR_FLOOR=None)
        self.assertEqual(build_filter_string(settings), "language:en")

    def test_rate_limit_detects_exhausted_daily_budget(self) -> None:
        class Response:
            headers: dict[str, str] = {}

            @staticmethod
            def json() -> dict[str, object]:
                return {"message": "Insufficient budget; daily budget is used up"}

        with self.assertRaisesRegex(RuntimeError, "OPENALEX_API_KEY"):
            _rate_limit_delay(Response(), attempt=0)  # type: ignore[arg-type]

    def test_rate_limit_stops_for_long_retry_after(self) -> None:
        class Response:
            headers: dict[str, str] = {}

            @staticmethod
            def json() -> dict[str, object]:
                return {"retryAfter": 3600}

        with self.assertRaisesRegex(RuntimeError, "3600 seconds"):
            _rate_limit_delay(Response(), attempt=0)  # type: ignore[arg-type]


class ClassificationTests(unittest.TestCase):
    def test_prompt_includes_domain_rules_and_paper_text(self) -> None:
        prompt = build_prompt("Steel slag binder", "The slag replaced cement.")

        self.assertIn("GGBFS/GGBS", prompt)
        self.assertIn("supporting excerpt", prompt)
        self.assertIn("The slag replaced cement.", prompt)

    def test_valid_result_requires_verbatim_excerpt(self) -> None:
        abstract = "Steel slag replaced part of the cement binder."
        result = {
            "decision": "Yes",
            "confidence": "High",
            "primary_application": "binder/SCM",
            "slag_type": "EAF",
            "rationale": "The abstract reports binder use.",
            "supporting_excerpt": "replaced part of the cement binder",
        }

        parsed = parse_classification(json.dumps(result), abstract)

        self.assertEqual(parsed["decision"], "Yes")

    def test_rejects_excerpt_not_in_abstract(self) -> None:
        result = {
            "decision": "Yes",
            "confidence": "High",
            "primary_application": "binder/SCM",
            "slag_type": "EAF",
            "rationale": "The abstract reports binder use.",
            "supporting_excerpt": "invented quotation",
        }

        with self.assertRaises(ValueError):
            parse_classification(json.dumps(result), "The abstract contains no such quote.")


if __name__ == "__main__":
    unittest.main()