"""Offline tests for retrieval-audit experiments."""

import csv
import tempfile
import unittest
from pathlib import Path

from paper_collection_pipeline.experiments.retrieval_audit import (
    QUERY_PROFILES,
    _sample_pages,
)
from paper_collection_pipeline.experiments.summarize_retrieval_audit import (
    summarize_labels,
)


class RetrievalAuditTests(unittest.TestCase):
    def test_audit_profiles_separate_binder_carbonation_and_characterization(self) -> None:
        self.assertIn("binder_current", QUERY_PROFILES)
        self.assertIn("carbonation", QUERY_PROFILES)
        self.assertIn("characterization", QUERY_PROFILES)
        self.assertNotEqual(
            QUERY_PROFILES["binder_current"],
            QUERY_PROFILES["binder_strict"],
        )

    def test_page_sample_covers_first_middle_and_last_accessible_pages(self) -> None:
        self.assertEqual(_sample_pages(50), [1])
        self.assertEqual(_sample_pages(25_000), [1, 50, 100])

    def test_label_summary_groups_results_by_profile(self) -> None:
        rows = [
            {
                "openalex_id": "W1",
                "matched_profiles": "binder_current; binder_strict",
                "human_label": "Yes",
            },
            {
                "openalex_id": "W2",
                "matched_profiles": "binder_current",
                "human_label": "No",
            },
            {
                "openalex_id": "W3",
                "matched_profiles": "carbonation",
                "human_label": "",
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            sample_path = Path(directory) / "sample.csv"
            with sample_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            summary = summarize_labels(sample_path)

        self.assertEqual(summary["labeled_unique_works"], 2)
        self.assertEqual(summary["unlabeled_unique_works"], 1)
        self.assertEqual(summary["profiles"]["binder_current"]["yes"], 1)
        self.assertEqual(summary["profiles"]["binder_current"]["no"], 1)
        self.assertEqual(summary["profiles"]["binder_strict"]["yes"], 1)

    def test_invalid_human_label_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sample_path = Path(directory) / "sample.csv"
            with sample_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=("openalex_id", "matched_profiles", "human_label"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "openalex_id": "W1",
                        "matched_profiles": "binder_current",
                        "human_label": "Maybe",
                    }
                )

            with self.assertRaises(ValueError):
                summarize_labels(sample_path)


if __name__ == "__main__":
    unittest.main()