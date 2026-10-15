"""Coverage tests for the resumable year archive (no network)."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import expand_library as expansion


class ExpansionTests(unittest.TestCase):
    def test_cached_revision_history_preserves_first_post_in_uncovered_month(self):
        with tempfile.TemporaryDirectory(dir=expansion.DATA) as temp:
            root = Path(temp)
            batch = root / "batch"
            batch.mkdir()
            record = {"id": "older-first-post", "published": "2025-12-20",
                      "provenance": {"fetched_at": "2026-10-14T00:00:00Z"}}
            payload = {"records": [record], "coverage": {"source": "biorxiv", "status": "complete",
                "checked_at": "2026-10-15T00:00:00Z", "previous_checked_at": "2026-10-14T01:00:00Z",
                "window": {"start": "2026-06-12", "end": "2026-07-11"}}}
            (batch / "biorxiv.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(expansion, "ARCHIVE", root):
                results, _ = expansion.collect(date(2025, 10, 15), date(2026, 10, 15))
            self.assertEqual(results[2]["records"], [record])
            withdrawn = root / "withdrawn"
            withdrawn.mkdir()
            exclusion = {"records": [], "coverage": {"source": "biorxiv", "status": "complete",
                "checked_at": "2026-10-14T12:00:00Z", "excluded_ids": [record["id"]],
                "window": {"start": "2026-09-12", "end": "2026-10-12"}}}
            (withdrawn / "biorxiv.json").write_text(json.dumps(exclusion), encoding="utf-8")
            with patch.object(expansion, "ARCHIVE", root):
                results, _ = expansion.collect(date(2025, 10, 15), date(2026, 10, 15))
            self.assertEqual(results[2]["records"], [])

    def test_fresh_complete_window_does_not_resurrect_removed_work(self):
        with tempfile.TemporaryDirectory(dir=expansion.DATA) as temp:
            root = Path(temp)
            old, fresh = root / "z-old", root / "a-new"
            old.mkdir(); fresh.mkdir()
            old_payload = {"records": [{"id": "gone", "published": "2026-01-15"},
                                       {"id": "outside", "published": "2026-01-02"}],
                           "coverage": {"source": "arxiv", "status": "complete",
                                        "checked_at": "2026-02-01T00:00:00Z",
                                        "window": {"start": "2026-01-01", "end": "2026-01-31"}}}
            fresh_payload = {"records": [], "coverage": {"source": "arxiv", "status": "complete",
                "checked_at": "2026-02-02T00:00:00Z",
                "window": {"start": "2026-01-10", "end": "2026-01-31"}}}
            for folder, payload in [(old, old_payload), (fresh, fresh_payload)]:
                (folder / "arxiv.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(expansion, "ARCHIVE", root):
                results, _ = expansion.collect(date(2026, 1, 1), date(2026, 1, 31))
                self.assertEqual([r["id"] for r in results[0]["records"]], ["outside"])
                fresh_payload["coverage"]["status"] = "failed"
                (fresh / "arxiv.json").write_text(json.dumps(fresh_payload), encoding="utf-8")
                results, _ = expansion.collect(date(2026, 1, 1), date(2026, 1, 31))
                self.assertEqual({r["id"] for r in results[0]["records"]}, {"gone", "outside"})
                fresh_payload["coverage"]["status"] = "complete"
                (fresh / "arxiv.json").write_text(json.dumps(fresh_payload), encoding="utf-8")
                later = root / "later-narrow-refresh"
                later.mkdir()
                carried = {"records": [{"id": "gone", "published": "2026-01-15",
                           "provenance": {"fetched_at": "2026-02-01T00:00:00Z"}}],
                           "coverage": {"source": "arxiv", "status": "complete",
                           "checked_at": "2026-02-03T00:00:00Z", "previous_checked_at": "2026-02-02T00:00:00Z",
                           "window": {"start": "2026-02-01", "end": "2026-02-28"}}}
                (later / "arxiv.json").write_text(json.dumps(carried), encoding="utf-8")
                results, _ = expansion.collect(date(2026, 1, 1), date(2026, 2, 28))
                self.assertEqual([r["id"] for r in results[0]["records"]], ["outside"])

    def test_calendar_boundaries(self):
        self.assertEqual(expansion.year_before(date(2024, 2, 29)), date(2023, 2, 28))
        self.assertEqual(expansion.month_before(date(2026, 3, 31)), date(2026, 2, 28))

    def test_no_snapshot_is_a_real_gap(self):
        lo, hi = date(2025, 10, 12), date(2026, 10, 12)
        self.assertEqual(expansion.uncovered(lo, hi, []), [(lo, hi)])

    def test_overlap_and_adjacency_do_not_create_gaps(self):
        lo, hi = date(2026, 1, 1), date(2026, 1, 31)
        spans = [(lo, date(2026, 1, 10)), (date(2026, 1, 10), date(2026, 1, 20)),
                 (date(2026, 1, 21), hi)]
        self.assertEqual(expansion.uncovered(lo, hi, spans), [])

    def test_gap_dates_are_inclusive(self):
        lo, hi = date(2026, 1, 1), date(2026, 1, 31)
        self.assertEqual(expansion.uncovered(lo, hi, [(date(2026, 1, 12), hi)]),
                         [(lo, date(2026, 1, 11))])

    def test_failed_batch_keeps_records_without_claiming_coverage(self):
        with tempfile.TemporaryDirectory(dir=expansion.DATA) as temp:
            root = Path(temp)
            batch = root / "batch"
            batch.mkdir()
            payload = {"records": [{"id": "preserved"}], "coverage": {
                "source": "arxiv", "status": "failed", "window": {
                    "start": "2026-01-01", "end": "2026-01-31"}, "failures": [{"error": "offline"}]}}
            (batch / "arxiv.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(expansion, "ARCHIVE", root):
                results, gaps = expansion.collect(date(2026, 1, 1), date(2026, 1, 31))
            arxiv = results[0]
            self.assertEqual(arxiv["records"], [{"id": "preserved"}])
            self.assertEqual(arxiv["coverage"]["status"], "partial")
            self.assertEqual(arxiv["coverage"]["missing_windows"], [{"start": "2026-01-01", "end": "2026-01-31"}])

    def test_usage_failure_is_retried_even_when_metadata_is_complete(self):
        with tempfile.TemporaryDirectory(dir=expansion.DATA) as temp:
            root = Path(temp)
            batch = root / "batch"
            batch.mkdir()
            payload = {"records": [], "coverage": {"source": "psyarxiv", "status": "complete",
                "window": {"start": "2026-01-01", "end": "2026-01-31"},
                "metrics_enrichment": {"failures": [{"error": "authors/usage unavailable"}]}}}
            (batch / "psyarxiv.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(expansion, "ARCHIVE", root):
                results, _ = expansion.collect(date(2026, 1, 1), date(2026, 1, 31))
            psy = results[1]["coverage"]
            self.assertEqual(psy["status"], "partial")
            self.assertTrue(psy["missing_windows"])
            self.assertTrue(psy["metrics_enrichment"]["failures"])


if __name__ == "__main__":
    unittest.main()
