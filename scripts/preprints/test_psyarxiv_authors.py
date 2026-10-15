"""Deleted-account recovery must not silently misattribute a manuscript."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from psyarxiv import _authors, _registered_authors


class AuthorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = SimpleNamespace(cache_dir=Path("unused-mocked-cache"), refresh=False)
        self.resource = {"id": "abc_v1", "attributes": {"title": "Learning with AI"},
                         "links": {"preprint_doi": "https://doi.org/10.31234/osf.io/abc_v1"}}
        self.message = {"DOI": "10.31234/osf.io/abc_v1", "title": ["Learning with AI"],
                        "author": [{"given": "Jane", "family": "Doe"}, {"given": "John", "family": "Smith"}]}

    def recover(self, message):
        with patch("psyarxiv.HTTPClient") as http:
            http.return_value.json.return_value = ({"message": message}, {"fetched_at": "2026-10-15T13:00:00Z"})
            return _registered_authors(self.resource, ["Jane Doe", None], self.client)

    def test_exact_registration_recovers_complete_ordered_list_and_provenance(self):
        self.assertEqual(self.recover(self.message), ["Jane Doe", "John Smith"])
        self.assertIn("api.crossref.org/works/", self.resource["_author_provenance"]["source_url"])

    def test_rejects_wrong_doi_title_count_or_known_author(self):
        for field, value in [("DOI", "10.31234/osf.io/abc_v2"), ("title", ["Other work"]),
                             ("author", self.message["author"][:1]),
                             ("author", list(reversed(self.message["author"]))),
                             ("author", [{"given": "Jane", "family": "Doe"}, {}])]:
            with self.subTest(field=field, value=value):
                message = copy.deepcopy(self.message)
                message[field] = value
                with self.assertRaises(RuntimeError):
                    self.recover(message)

    def test_omitted_middle_initial_is_allowed_but_conflicting_names_are_not(self):
        message = copy.deepcopy(self.message)
        message["author"][0]["given"] = "Jane Q."
        self.assertEqual(self.recover(message)[0], "Jane Q. Doe")
        for known in ("Jane R. Doe", "Janet Doe", "Jane Quinn Doe"):
            with self.subTest(known=known), patch("psyarxiv.HTTPClient") as http:
                http.return_value.json.return_value = ({"message": message}, {"fetched_at": "2026-10-15T13:00:00Z"})
                with self.assertRaises(RuntimeError):
                    _registered_authors(self.resource, [known, None], self.client)

    def test_only_deleted_user_failure_uses_registration(self):
        self.resource["embeds"] = {"bibliographic_contributors": {"data": [
            {"id": "one", "attributes": {"index": 0, "unregistered_contributor": "Jane Doe"}},
            {"id": "two", "attributes": {"index": 1}, "relationships": {
                "users": {"links": {"related": {"href": "https://api.osf.io/v2/users/deleted/"}}}}}
        ]}}
        for code in (410, 503):
            self.client.get = lambda url, code=code: (_ for _ in ()).throw(RuntimeError(f"HTTP Error {code}: Gone"))
            with patch("psyarxiv._registered_authors", return_value=["Jane Doe", "John Smith"]) as recover:
                if code == 410:
                    self.assertEqual(_authors(self.resource, self.client), ["Jane Doe", "John Smith"])
                    self.assertEqual(recover.call_args.args[1], ["Jane Doe", None])
                else:
                    with self.assertRaises(RuntimeError):
                        _authors(self.resource, self.client)
                    recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
