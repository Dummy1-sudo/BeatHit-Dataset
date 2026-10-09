"""Offline regression coverage for separate verified-list and candidate schemas."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".github" / "parallel"))

import common
import finalize_list
from music_megalist.cover_candidates import candidate_csv_errors


class CandidatePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"
        self.folder = self.data / "language_covers" / "norwegian"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "norwegian_cover_candidates.csv"
        self.headers = ["source", "source_id", "youtube_id", "video_title", "url",
                        "language_target", "review_status", "original_title", "original_artist",
                        "cover_verified", "language_verified", "cover_evidence_url", "language_evidence_url"]
        self.row = {"source": "youtube", "source_id": "abcdefghijk",
                    "youtube_id": "abcdefghijk", "video_title": "Example cover",
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "language_target": "no", "review_status": "unverified"}

    def write_candidates(self, *rows):
        with self.path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.headers)
            writer.writeheader()
            writer.writerows(rows)

    def test_unverified_candidates_have_their_own_valid_schema(self):
        self.write_candidates(self.row)
        self.assertEqual(candidate_csv_errors(self.path), [])

    def test_wrong_language_is_rejected(self):
        self.write_candidates({**self.row, "language_target": "sv"})
        self.assertTrue(any("CANDIDATE_LANGUAGE" in e for e in candidate_csv_errors(self.path)))

    def test_duplicate_candidate_is_rejected(self):
        self.write_candidates(self.row, self.row)
        self.assertTrue(any("CANDIDATE_DUPLICATE" in e for e in candidate_csv_errors(self.path)))

    def test_verified_claim_requires_cover_and_language_evidence(self):
        self.write_candidates({**self.row, "review_status": "verified"})
        self.assertTrue(any("CANDIDATE_UNSUPPORTED_VERIFICATION" in e for e in candidate_csv_errors(self.path)))

    def test_only_verified_partitions_use_songrow_validator(self):
        self.write_candidates(self.row)
        part = self.folder / "norwegian_covers_part_001.csv"
        part.write_text("title,main_artist\n", encoding="utf-8")
        with patch.object(common, "DATA", self.data):
            files = list(common.iter_csvs("cover_norwegian"))
        self.assertEqual(files, [part])

    def test_candidate_only_worker_finalizes_without_claiming_completion(self):
        self.write_candidates(self.row)
        fragment_path = Path(self.temporary.name) / "status.json"
        fragment_path.write_text(json.dumps({"dataset": {"target": 10000, "complete": False}}), encoding="utf-8")
        with patch.object(finalize_list, "DATA", self.data), \
             patch.object(common, "DATA", self.data), \
             patch.object(finalize_list, "status_path", lambda target: fragment_path), \
             patch.object(sys, "argv", ["finalize_list.py", "cover_norwegian"]):
            self.assertEqual(finalize_list.main(), 0)
        status = json.loads(fragment_path.read_text(encoding="utf-8"))
        self.assertEqual(status["validation_errors"], [])
        self.assertEqual(status["dataset"]["materialized_rows"], 0)
        self.assertFalse(status["dataset"]["complete"])

    def test_10000_verified_rows_meet_target_and_candidates_do_not(self):
        self.write_candidates(self.row)
        fragment_path = Path(self.temporary.name) / "status.json"
        fragment_path.write_text(json.dumps({"dataset": {"target": 10000}}), encoding="utf-8")
        with patch.object(finalize_list, "DATA", self.data), \
             patch.object(common, "DATA", self.data), \
             patch.object(finalize_list, "_finalize", lambda target: 10000), \
             patch.object(finalize_list, "status_path", lambda target: fragment_path), \
             patch.object(sys, "argv", ["finalize_list.py", "cover_norwegian"]):
            self.assertEqual(finalize_list.main(), 0)
        status = json.loads(fragment_path.read_text(encoding="utf-8"))
        self.assertTrue(status["dataset"]["complete"])
        self.assertEqual(status["dataset"]["materialized_rows"], 10000)


if __name__ == "__main__":
    unittest.main()
