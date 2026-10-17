"""An exhausted upstream outage must not stall each remaining article."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from psyarxiv import OSFClient
from update_library import main


class OutageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / "data/preprints")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_exhausted_transient_failure_pauses_network_but_keeps_cache_replay(self):
        client = OSFClient(self.folder)
        url = "https://api.osf.io/v2/preprints/one/"
        for error in (HTTPError(url, 503, "Unavailable", {}, None), TimeoutError("timed out")):
            with self.subTest(error=type(error).__name__):
                client.network_failure = None
                with patch("psyarxiv.urlopen", side_effect=error) as request, patch("psyarxiv.time.sleep"):
                    with self.assertRaisesRegex(RuntimeError, "bounded retries"):
                        client.get(url)
                    self.assertEqual(request.call_count, 5)
                    with self.assertRaisesRegex(RuntimeError, "paused"):
                        client.get("https://api.osf.io/v2/preprints/two/")
                    self.assertEqual(request.call_count, 5)
                    cached_url = "https://api.osf.io/v2/preprints/cached/"
                    key = hashlib.sha256(cached_url.encode()).hexdigest()[:24]
                    (self.folder / ("psyarxiv-" + key + ".json")).write_text(json.dumps({
                        "url": cached_url, "fetched_at": "2026-10-16T13:00:00Z", "response": {"data": {"id": "cached"}}
                    }), encoding="utf-8")
                    self.assertEqual(client.get(cached_url)[0]["data"]["id"], "cached")
                    self.assertEqual(request.call_count, 5)

    def test_deleted_account_does_not_disable_other_network_requests(self):
        client = OSFClient(self.folder)
        url = "https://api.osf.io/v2/users/deleted/"
        with patch("psyarxiv.urlopen", side_effect=[HTTPError(url, 410, "Gone", {}, None),
                                                   io.BytesIO(b'{"data":{"id":"healthy"}}')]) as request, patch("psyarxiv.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.get(url)
            self.assertIsNone(client.network_failure)
            self.assertEqual(client.get("https://api.osf.io/v2/preprints/healthy/")[0]["data"]["id"], "healthy")
            self.assertEqual(request.call_count, 2)

    def record(self):
        return {"id": "psyarxiv:one", "source": "psyarxiv", "source_id": "one_v1",
                "title": "Reinforcement learning in humans", "abstract": "Human participants learn rewards in a reinforcement learning task.",
                "authors": ["Jane Doe"], "published": "2026-10-10", "updated": "2026-10-10", "version": 1,
                "url": "https://osf.io/preprints/psyarxiv/one_v1/", "provenance": {"first_post_verified": True},
                "metrics": {"views": 2, "downloads": 1, "observed_at": "2026-10-16T13:00:00Z",
                            "source_url": None, "scope": "all-versions", "coverage_complete": True}}

    def run_pipeline(self):
        return main(["--sources", "psyarxiv", "--start", "2026-09-17", "--end", "2026-10-17",
                     "--full", "--strict", "--data-dir", str(self.folder), "--output", str(self.folder / "selected.json")])

    def test_failed_metadata_retains_prior_counters_without_rechecking_outage(self):
        original = {"records": [self.record()], "coverage": {"source": "psyarxiv", "status": "complete",
                    "checked_at": "2026-10-16T13:00:00Z"}}
        (self.folder / "psyarxiv.json").write_text(json.dumps(original), encoding="utf-8")
        module = SimpleNamespace(harvest=Mock(side_effect=RuntimeError("503 unavailable")), enrich_metrics=Mock())
        with patch("update_library.importlib.import_module", return_value=module):
            self.assertEqual(self.run_pipeline(), 1)
        module.enrich_metrics.assert_not_called()
        saved = json.loads((self.folder / "psyarxiv.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["records"][0]["metrics"], original["records"][0]["metrics"])
        self.assertEqual(saved["coverage"]["status"], "failed")
        self.assertIn("skipped", saved["coverage"]["metrics_refresh"])

    def test_enrichment_progress_survives_later_interruption(self):
        result = {"records": [self.record()], "coverage": {"source": "psyarxiv", "status": "complete"}}
        def enrich(records, cache_dir, *, refresh, on_progress):
            on_progress({"status": "in_progress", "checked": 1, "enriched": 1, "failures": []})
            saved = json.loads((self.folder / "psyarxiv.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["coverage"]["metrics_enrichment"]["checked"], 1)
            raise RuntimeError("interrupted")
        module = SimpleNamespace(harvest=Mock(return_value=result), enrich_metrics=enrich)
        with patch("update_library.importlib.import_module", return_value=module):
            self.assertEqual(self.run_pipeline(), 1)
        saved = json.loads((self.folder / "psyarxiv.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["coverage"]["metrics_enrichment"]["status"], "in_progress")
        self.assertIn("interrupted", saved["coverage"]["metrics_error"])


if __name__ == "__main__":
    unittest.main()
