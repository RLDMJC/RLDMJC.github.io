"""Verified withdrawals finish a check; unavailable evidence still fails it."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from psyarxiv import enrich_metrics
from update_library import build_library


class WithdrawalTests(unittest.TestCase):
    def record(self):
        return {"id": "psyarxiv:one", "source": "psyarxiv", "source_id": "one_v1",
                "title": "Reinforcement learning in humans", "abstract": "Human participants learn rewards in a reinforcement learning task.",
                "authors": ["Jane Doe"], "published": "2026-02-14", "updated": "2026-02-14",
                "url": "https://osf.io/preprints/psyarxiv/one_v1/", "version": 1,
                "provenance": {"first_post_verified": True}}

    def test_verified_withdrawal_is_excluded_without_leaving_usage_gap(self):
        record = self.record()
        client = Mock(requests=1, cached=0)
        client.collection.return_value = ([{"id": "one_v2", "attributes": {
            "version": 2, "date_withdrawn": "2026-10-21T12:00:00Z"}}], ["2026-10-23T13:00:00Z"])
        progress = []
        with patch("psyarxiv.OSFClient", return_value=client):
            summary = enrich_metrics([record], Path("unused"), on_progress=progress.append)
        self.assertEqual(summary["failures"], [])
        self.assertEqual(summary["enriched"], 0)
        self.assertEqual(summary["withdrawals"][0]["source_id"], "one_v2")
        self.assertEqual(progress[-1]["withdrawals"], summary["withdrawals"])
        self.assertEqual(record["metrics"]["scope"], "withdrawn")
        self.assertIsNone(record["metrics"]["views"])
        client.get.assert_not_called()
        library, _ = build_library([{"records": [record], "coverage": {
            "source": "psyarxiv", "status": "complete", "window": {"start": "2026-02-12", "end": "2026-03-11"},
            "metrics_enrichment": summary}}], "2026-02-12", "2026-03-11", {})
        self.assertEqual(library["papers"], [])

    def test_unverified_stale_withdrawal_does_not_hide_network_failure(self):
        record = self.record()
        record["withdrawn"] = True
        client = Mock(requests=1, cached=0)
        client.collection.side_effect = RuntimeError("endpoint unavailable")
        with patch("psyarxiv.OSFClient", return_value=client):
            summary = enrich_metrics([record], Path("unused"))
        self.assertEqual(summary["withdrawals"], [])
        self.assertEqual(len(summary["failures"]), 1)
        self.assertIn("endpoint unavailable", summary["failures"][0]["error"])


if __name__ == "__main__":
    unittest.main()
