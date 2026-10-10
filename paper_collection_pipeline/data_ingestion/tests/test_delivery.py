"""Offline tests for legacy-compatible PDF delivery behavior."""

import tempfile
import unittest
from pathlib import Path

from paper_collection_pipeline.data_ingestion.deliver_papers import (
    _pdf_urls,
    _prepare_accepted_papers,
)
from paper_collection_pipeline.data_ingestion.io_utils import (
    is_valid_pdf,
    openalex_work_filename,
)


class PdfNamingTests(unittest.TestCase):
    def test_uses_openalex_work_id_as_legacy_pdf_name(self) -> None:
        self.assertEqual(
            openalex_work_filename("https://openalex.org/W3177678890"),
            "W3177678890.pdf",
        )

    def test_rejects_non_work_identifier(self) -> None:
        with self.assertRaises(ValueError):
            openalex_work_filename("https://openalex.org/A123")

    def test_checks_pdf_signature(self) -> None:
        self.assertTrue(is_valid_pdf(b"%PDF-1.7 sample"))
        self.assertFalse(is_valid_pdf(b"<html>login page"))


class PdfDeliveryTests(unittest.TestCase):
    def test_extracts_both_supported_openalex_pdf_locations(self) -> None:
        record = {
            "open_access": {"oa_url": "https://example.org/oa.pdf"},
            "primary_location": {"pdf_url": "https://example.org/paper.pdf"},
        }
        self.assertEqual(
            _pdf_urls(record),
            ["https://example.org/oa.pdf", "https://example.org/paper.pdf"],
        )

    def test_reuses_existing_named_pdf_and_routes_missing_pdf_to_manual_list(self) -> None:
        records = [
            {
                "id": "https://openalex.org/W1234567890",
                "decision": "Yes",
                "title": "Already downloaded",
                "open_access": {"oa_url": None},
                "primary_location": {"pdf_url": None},
            },
            {
                "id": "https://openalex.org/W9876543210",
                "decision": "Yes",
                "title": "No PDF URL",
                "open_access": {"oa_url": None},
                "primary_location": {"pdf_url": None},
            },
            {
                "id": "https://openalex.org/W1111111111",
                "decision": "No",
                "title": "Rejected paper",
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            pdf_dir = Path(directory)
            (pdf_dir / "W1234567890.pdf").write_bytes(b"%PDF-1.7 test")
            relevant, manual, stats = _prepare_accepted_papers(records, pdf_dir)

        self.assertEqual(stats["accepted"], 2)
        self.assertEqual(stats["pdf_ready"], 1)
        self.assertEqual([row["title"] for row in relevant], [
            "Already downloaded",
            "No PDF URL",
        ])
        self.assertEqual(relevant[0]["local_pdf_file"], "W1234567890.pdf")
        self.assertEqual([row["title"] for row in manual], ["No PDF URL"])


if __name__ == "__main__":
    unittest.main()
