"""Validate the *discovery* CSV schema without treating candidates as song-list rows.

A candidate is not a confirmed cover.  No row from this file may contribute to
materialized_rows or a completed cover-list target merely by being present here.
"""
from __future__ import annotations

import csv
from pathlib import Path

LANGUAGE_CODES = {
    "russian": "ru", "japanese": "ja", "german": "de", "afrikaans": "af",
    "korean": "ko", "chinese": "zh", "ukrainian": "uk", "swedish": "sv",
    "norwegian": "no", "indonesian": "id",
}
REQUIRED = {"source", "source_id", "video_title", "language_target", "review_status"}
STATUSES = {"unverified", "needs_review", "rejected", "verified"}
EVIDENCE = ("original_title", "original_artist", "cover_evidence_url",
            "language_evidence_url")


def candidate_csv_errors(path: Path, *, expected_language: str | None = None) -> list[str]:
    """Report malformed candidates separately from canonical SongRow validation."""
    errors: list[str] = []
    if expected_language is None:
        expected_language = LANGUAGE_CODES.get(path.parent.name)
    seen: set[tuple[str, str]] = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = REQUIRED - set(reader.fieldnames or ())
            if missing:
                return [f"CANDIDATE_SCHEMA {path}: missing {sorted(missing)}"]
            for number, row in enumerate(reader, 2):
                if None in row:
                    errors.append(f"CANDIDATE_COLUMNS {path} line {number}")
                source = (row.get("source") or "").strip()
                source_id = (row.get("source_id") or "").strip()
                title = (row.get("video_title") or "").strip()
                language = (row.get("language_target") or "").strip()
                state = (row.get("review_status") or "").strip()
                if not source or not source_id or not title:
                    errors.append(f"CANDIDATE_REQUIRED {path} line {number}")
                if expected_language and language != expected_language:
                    errors.append(f"CANDIDATE_LANGUAGE {path} line {number}: {language!r} != {expected_language!r}")
                if state not in STATUSES:
                    errors.append(f"CANDIDATE_STATUS {path} line {number}: {state!r}")
                key = (source, source_id)
                if key in seen:
                    errors.append(f"CANDIDATE_DUPLICATE {path} line {number}: {key}")
                seen.add(key)
                if state == "verified":
                    if any(not (row.get(col) or "").strip() for col in EVIDENCE) or (
                        row.get("cover_verified") != "yes" or row.get("language_verified") != "yes"
                    ):
                        errors.append(f"CANDIDATE_UNSUPPORTED_VERIFICATION {path} line {number}")
                if len(errors) >= 200:
                    break
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"CANDIDATE_READ {path}: {type(exc).__name__}: {exc}")
    return errors
