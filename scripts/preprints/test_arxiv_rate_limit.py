"""Respect an exhausted rate limit without losing successful source evidence."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from arxiv import harvest
from common import FetchError, HTTPClient


def feed(total, entry=""):
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
            f'<o:totalResults>{total}</o:totalResults>{entry}</feed>').encode()


META = {"url": "https://export.arxiv.org/api/query", "fetched_at": "2026-10-19T13:00:00Z", "sha256": "test"}


class RateLimitTests(unittest.TestCase):
    def test_http_status_survives_bounded_retry_exhaustion(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / "data/preprints") as temp:
            client = HTTPClient(temp)
            error = HTTPError(META["url"], 429, "Too Many Requests", {}, None)
            with patch("common.urlopen", side_effect=error) as request, patch("common.time.sleep"):
                with self.assertRaises(FetchError) as caught:
                    client.get(META["url"])
                self.assertEqual(caught.exception.http_status, 429)
                self.assertEqual(request.call_count, client.retries)

    def run_harvest(self, responses, start="2026-05-01", end="2026-07-31"):
        client = SimpleNamespace(get=Mock(side_effect=responses), requests=0, cache_hits=0)
        with patch("arxiv.HTTPClient", return_value=client):
            result = harvest(start, end, Path("unused-mocked-cache"))
        return result, client

    def test_rate_limit_preserves_pages_and_marks_remaining_months_pending(self):
        entry = '<entry><id>https://arxiv.org/abs/2605.00001v1</id><title>Human learning</title><summary>Reward learning.</summary><published>2026-05-05T00:00:00Z</published><updated>2026-05-05T00:00:00Z</updated><author><name>Jane Doe</name></author></entry>'
        result, client = self.run_harvest([(feed(1), META), (feed(1, entry), META),
                                         FetchError("limited", http_status=429)])
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertTrue(result["coverage"]["rate_limited"])
        self.assertEqual(result["coverage"]["failures"][-1]["start"], "2026-07-01")
        self.assertEqual(result["coverage"]["failures"][-1]["status"], "not_attempted")

    def test_recursive_sibling_is_not_requested_after_exhausted_limit(self):
        result, client = self.run_harvest([(feed(1001), META), FetchError("limited", http_status=429)])
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(len(result["coverage"]["failures"]), 4)
        self.assertEqual(result["coverage"]["status"], "failed")

    def test_other_failures_do_not_pause_and_next_run_resumes(self):
        result, client = self.run_harvest([FetchError("unavailable", http_status=503), (feed(0), META), (feed(0), META)])
        self.assertEqual(client.get.call_count, 3)
        self.assertFalse(result["coverage"]["rate_limited"])
        result, client = self.run_harvest([(feed(0), META)] * 3)
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(result["coverage"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
