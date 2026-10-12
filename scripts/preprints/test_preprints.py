"""Boundary tests for source dates, conservative screening, and failed refreshes."""
import unittest
import json
import math
import tempfile
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arxiv import parse_atom
from common import FetchError
from screen import screen
from update_library import build_library, dedupe_versions, dedupe_cross_sources, normalized_metrics, within_window, main, exposure_fraction, positive_finite


def paper(**changes):
    base = {"id": "arxiv:1", "source": "arxiv", "source_id": "1", "title": "Reward learning in human participants",
            "abstract": "We tested reinforcement learning in human participants performing a reward learning task.",
            "authors": ["Jane Doe"], "url": "https://arxiv.org/abs/1", "doi": None,
            "published": "2025-10-12", "updated": "2025-10-12", "version": 1, "provenance": {"first_post_verified": True}}
    base.update(changes)
    return base


class ScreeningTests(unittest.TestCase):
    def test_biological_rl_and_pure_ai_distinction(self):
        self.assertIn("rl-learning", screen(paper())["categories"])
        pure = paper(title="Efficient reinforcement learning", abstract="We propose a reinforcement learning agent that improves Atari game scores.")
        self.assertEqual(screen(pure)["status"], "rejected")
        rlhf = paper(title="RLHF for language models", abstract="We train large language models by reinforcement learning from human feedback and human preferences.")
        self.assertNotIn("rl-learning", screen(rlhf)["categories"])
        self.assertEqual(screen(rlhf)["status"], "candidate")

    def test_general_ai_requires_explicit_bridge(self):
        unbridged = paper(title="Continual learning for language models", abstract="We introduce continual learning for large language models and report benchmark accuracy.")
        self.assertEqual(screen(unbridged)["status"], "candidate")
        bridged = paper(title="Continual learning for language models", abstract="We introduce continual learning for large language models, inspired by human learning and cognitive mechanisms.")
        self.assertIn("ai-implications", screen(bridged)["categories"])

    def test_medical_image_classification_not_cognitive_innovation(self):
        medical = paper(title="Brain tumor classification", abstract="We introduce a deep learning neural network for brain tumor image classification in human patients.")
        self.assertEqual(screen(medical)["status"], "rejected")

    def test_sample_efficiency_does_not_establish_human_implication(self):
        generic = paper(title="Sample efficient continual learning", abstract="We propose continual learning for neural networks with improved sample efficiency and knowledge transfer.")
        self.assertEqual(screen(generic)["status"], "candidate")

    def test_incidental_biology_and_collaborative_ml_excluded(self):
        dog = paper(title="Temporal difference between odorant pulses", abstract="We tested dogs behavioral responses to temporal difference between two odorant pulses.")
        self.assertEqual(screen(dog)["status"], "rejected")
        cells = paper(title="CellTRIP", abstract="We introduce reinforcement learning for single cell gene expression analysis using mouse cortical cell data.")
        self.assertNotIn("rl-learning", screen(cells)["categories"])
        cancer = paper(title="LaCONIC", abstract="We introduce deep neural networks with collaborative learning for multimodal cancer prediction.")
        self.assertEqual(screen(cancer)["status"], "rejected")
        equine = paper(title="Equine Edge AI", abstract="We introduce artificial intelligence for equine gait detection to reduce psychological harm.")
        self.assertEqual(screen(equine)["status"], "rejected")
        labels = paper(title="Enterprise RL", abstract="We train a language model using reinforcement learning. Human-labeled production tool-calling task benchmarks improve in accuracy.")
        self.assertNotIn("rl-learning", screen(labels)["categories"])
        robot = paper(title="Real world RL", abstract="We propose reinforcement learning for robot manipulation without human demonstrations or corrective actions. The robot performs learning on hardware.")
        self.assertNotIn("rl-learning", screen(robot)["categories"])
        evaluator = paper(title="Clinical correctness", abstract="We introduce machine learning clinical benchmarks that agree with human judgments using labels from reviewers.")
        self.assertNotIn("ai-cognition", screen(evaluator)["categories"])
        underwater = paper(title="Underwater RL beam pointing", abstract="This work addresses beam pointing for a moving vehicle subject to unknown ocean currents through deep reinforcement learning. The resulting agent optimizes beam steering, learning a policy that maintains a communications link.")
        self.assertNotIn("rl-learning", screen(underwater)["categories"])
        market = paper(title="Electricity market RL", abstract="Transmission capacities are updated while market participants simultaneously learn bidding strategies through deep reinforcement learning. Numerical results on the IEEE bus system show policy degradation of bidding agents.")
        self.assertNotIn("rl-learning", screen(market)["categories"])
        resets = paper(title="Robot policy adaptation", abstract="We present reinforcement learning for a robot foundation policy with minimal human intervention. Humans identify bottlenecks during setup and perform physical resets when needed.")
        self.assertNotIn("rl-learning", screen(resets)["categories"])


class DataBoundaryTests(unittest.TestCase):
    def test_first_post_not_revision_controls_window(self):
        versions = [paper(source="biorxiv", source_id="x", version=1, published="2025-10-01"),
                    paper(source="biorxiv", source_id="x", version=2, published="2026-01-01", updated="2026-01-01")]
        latest = dedupe_versions(versions)[0]
        self.assertEqual(latest["version"], 2)
        self.assertEqual(latest["published"], "2025-10-01")
        self.assertFalse(within_window(latest, "2025-10-12", "2026-10-12"))
        self.assertTrue(within_window(paper(), "2025-10-12", "2026-10-12"))

    def test_source_dedupe_requires_title_and_author_or_doi(self):
        a = paper()
        b = paper(id="psyarxiv:x", source="psyarxiv", source_id="x")
        c = paper(id="biorxiv:y", source="biorxiv", source_id="y", authors=["Different Author"])
        self.assertEqual(len(dedupe_cross_sources(dedupe_versions([a, b]))), 1)
        self.assertEqual(len(dedupe_cross_sources(dedupe_versions([a, c]))), 2)
        formatted = paper(id="biorxiv:z", source="biorxiv", source_id="z", authors=["Doe, J."])
        self.assertEqual(len(dedupe_cross_sources(dedupe_versions([a, formatted]))), 1)

    def test_missing_metrics_remain_null_not_zero(self):
        result = {"records": [paper()], "coverage": {"source": "arxiv", "status": "failed", "fetched": 0, "failures": [{"error": "timeout"}]}}
        library, audit = build_library([result], "2025-10-12", "2026-10-12")
        self.assertIsNone(library["papers"][0]["metrics"]["views_age_adjusted"])
        self.assertEqual(library["coverage"][0]["status"], "failed")
        self.assertEqual(library["ranking"]["measured_papers"], 0)

    def test_zero_day_age_is_safe_and_observation_date_used(self):
        record = paper(published="2026-10-12", metrics={"views": 10, "downloads": 2, "observed_at": "2026-10-12T10:00:00Z", "scope": "all-versions", "coverage_complete": True})
        normalized_metrics(record, "2026-10-13")
        self.assertEqual(record["metrics"]["age_days"], 1)
        self.assertAlmostEqual(record["metrics"]["downloads_age_adjusted"], 2 / (-math.expm1(-1 / 7)))

    def test_unverified_first_post_withheld(self):
        record = paper(version=2, provenance={"first_post_verified": False})
        library, audit = build_library([{"records": [record], "coverage": {"source": "arxiv", "status": "complete", "fetched": 1}}], "2025-10-12", "2026-10-12")
        self.assertEqual(len(library["papers"]), 0)
        self.assertEqual(audit[0]["screening"]["status"], "candidate")

    def test_atom_error_is_failure_not_empty_success(self):
        with self.assertRaises(FetchError):
            parse_atom(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><summary>bad query</summary></entry></feed>', {"url": "x"})

    def test_psyarxiv_versioned_source_ids_merge(self):
        a = paper(id="psyarxiv:x", source="psyarxiv", source_id="x", version=1)
        b = paper(id="psyarxiv:x", source="psyarxiv", source_id="x_v2", version=2, published="2026-01-01", updated="2026-01-01")
        merged = dedupe_versions([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["published"], "2025-10-12")
        self.assertEqual(merged[0]["version"], 2)

    def test_failed_refresh_preserves_last_good_papers_and_failure(self):
        fixture_root = Path(__file__).resolve().parents[2] / "data/preprints"
        fixture_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixture_root) as directory:
            data = Path(directory)
            self.assertTrue(data.resolve().is_relative_to(fixture_root.resolve()))
            prior = {"records": [paper(published="2026-09-12")], "coverage": {"source": "arxiv", "status": "complete", "window": {"start": "2026-09-12", "end": "2026-10-11"}}}
            (data / "arxiv.json").write_text(json.dumps(prior), encoding="utf-8")
            adapter = SimpleNamespace(harvest=lambda *args, **kwargs: (_ for _ in ()).throw(FetchError("service unavailable")))
            with patch("update_library.importlib.import_module", return_value=adapter):
                exit_code = main(["--start", "2026-09-12", "--end", "2026-10-12", "--sources", "arxiv", "--strict", "--data-dir", str(data), "--output", str(data / "library.json")])
            library = json.loads((data / "library.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(library["papers"]), 1)
            self.assertEqual(library["coverage"][0]["status"], "failed")
            self.assertEqual(library["coverage"][0]["preserved_previous_records"], 1)

    def test_complete_full_source_removes_absent_current_window_work(self):
        fixture_root = Path(__file__).resolve().parents[2] / "data/preprints"
        with tempfile.TemporaryDirectory(dir=fixture_root) as directory:
            data = Path(directory)
            self.assertTrue(data.resolve().is_relative_to(fixture_root.resolve()))
            prior = {"records": [paper(published="2026-09-12")], "coverage": {"source": "arxiv", "status": "complete", "window": {"start": "2026-09-12", "end": "2026-10-11"}}}
            (data / "arxiv.json").write_text(json.dumps(prior), encoding="utf-8")
            adapter = SimpleNamespace(harvest=lambda *args, **kwargs: {"records": [], "coverage": {"source": "arxiv", "status": "complete", "fetched": 0}})
            with patch("update_library.importlib.import_module", return_value=adapter):
                exit_code = main(["--start", "2026-09-12", "--end", "2026-10-12", "--sources", "arxiv", "--full", "--strict", "--data-dir", str(data), "--output", str(data / "library.json")])
            library = json.loads((data / "library.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(library["papers"]), 0)
            self.assertEqual(library["coverage"][0]["preserved_previous_records"], 0)

    def test_partial_metrics_withheld_from_exposure_ranking(self):
        record = paper(metrics={"views": 10, "downloads": 2, "observed_at": "2026-10-12T10:00:00Z", "scope": "partial", "coverage_complete": False})
        normalized_metrics(record, "2026-10-12")
        self.assertIsNone(record["metrics"]["downloads_age_adjusted"])

    def test_cross_source_latest_display_earliest_date_and_union(self):
        a = paper(doi="10.1000/example", categories=["rl-learning"], tags=["RL"])
        b = paper(id="psyarxiv:x", source="psyarxiv", source_id="x", doi="10.1000/example", title="Revised title", abstract="Newer abstract", published="2026-01-01", updated="2026-02-01", categories=["ai-cognition"], tags=["cognition"])
        result = dedupe_cross_sources(dedupe_versions([a, b]))[0]
        self.assertEqual(result["title"], "Revised title")
        self.assertEqual(result["published"], "2025-10-12")
        self.assertEqual(set(result["categories"]), {"rl-learning", "ai-cognition"})

    def test_source_withdrawal_excluded_even_with_include_override(self):
        r = paper(withdrawn=True)
        library, audit = build_library([{"records": [r], "coverage": {"source": "arxiv", "status": "complete", "fetched": 1}}], "2025-10-12", "2026-10-12", {"arxiv:1": {"action": "include", "categories": ["rl-learning"]}})
        self.assertEqual(len(library["papers"]), 0)
        self.assertEqual(audit[0]["screening"]["status"], "rejected")


class ExposureCorrectionTests(unittest.TestCase):
    def normalized(self, age, tau=7, count=100):
        from datetime import datetime, timedelta, timezone
        first = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
        observed = first + timedelta(days=age)
        r = paper(published="2026-09-12", updated="2026-10-12", first_posted_at=first.isoformat(),
                  metrics={"views": count, "downloads": count // 2, "scope": "all-versions", "coverage_complete": True,
                           "first_posted_at": first.isoformat(), "observed_at": observed.isoformat()})
        normalized_metrics(r, "2026-10-12", tau)
        return r

    def test_old_paper_score_saturates_at_raw_total(self):
        r = self.normalized(700)
        self.assertEqual(r["metrics"]["views_age_adjusted"], 100)
        self.assertEqual(r["metrics"]["exposure_fraction"], 1)

    def test_young_paper_has_saturating_exposure_correction(self):
        r = self.normalized(7)
        expected = 100 / (1 - math.exp(-1))
        self.assertAlmostEqual(r["metrics"]["views_age_adjusted"], expected)
        self.assertGreater(r["metrics"]["views_age_adjusted"], 100)
        self.assertNotIn("views_per_day", r["metrics"])

    def test_fixed_count_score_decreases_monotonically_toward_raw_count(self):
        scores = [self.normalized(age)["metrics"]["views_age_adjusted"] for age in (1, 2, 7, 21, 70)]
        self.assertTrue(all(a > b for a, b in zip(scores, scores[1:])))
        self.assertTrue(all(score >= 100 for score in scores))

    def test_small_age_has_one_day_floor_and_zero_counts_stay_zero(self):
        self.assertEqual(self.normalized(0.1)["metrics"]["views_age_adjusted"], self.normalized(1)["metrics"]["views_age_adjusted"])
        self.assertEqual(self.normalized(1, count=0)["metrics"]["views_age_adjusted"], 0)

    def test_expm1_avoids_small_ratio_cancellation(self):
        fraction = exposure_fraction(1, 1e16)
        self.assertGreater(fraction, 0)
        self.assertTrue(math.isclose(fraction, 1e-16, rel_tol=1e-14))
        self.assertTrue(math.isfinite(self.normalized(1, tau=1e16)["metrics"]["views_age_adjusted"]))

    def test_tau_rejects_nonpositive_and_nonfinite_values(self):
        import argparse
        for value in (0, -1, float("nan"), float("inf"), "bad"):
            with self.assertRaises(argparse.ArgumentTypeError):
                positive_finite(value)
        self.assertEqual(positive_finite("14"), 14)

    def test_first_version_timestamp_controls_age_not_latest_revision(self):
        r = self.normalized(3.5)
        self.assertEqual(r["published"], "2026-09-12")
        self.assertEqual(r["updated"], "2026-10-12")
        self.assertEqual(r["metrics"]["age_days"], 3.5)
        self.assertEqual(r["metrics"]["age_basis"], "first-post-timestamp")
        r["metrics"]["first_post_date"] = "2026-09-13"
        normalized_metrics(r, "2026-10-12")
        self.assertEqual(r["metrics"]["age_days"], 3.5)

    def test_date_only_source_precision_is_explicit(self):
        r = paper(published="2026-09-12", metrics={"views": None, "downloads": None, "observed_at": "2026-09-15T12:00:00Z"})
        normalized_metrics(r, "2026-10-12")
        self.assertEqual(r["metrics"]["age_days"], 3.5)
        self.assertEqual(r["metrics"]["age_basis"], "first-post-date-midnight-UTC")
        self.assertIsNone(r["metrics"]["views_age_adjusted"])

    def test_missing_invalid_or_earlier_observation_has_no_age_or_score(self):
        for observed in (None, "not-a-time", "2026-09-11T12:00:00Z"):
            r = paper(published="2026-09-12", first_posted_at="2026-09-12T12:00:00Z",
                      metrics={"views": 100, "downloads": 50, "observed_at": observed,
                               "scope": "all-versions", "coverage_complete": True})
            normalized_metrics(r, "2026-10-12")
            self.assertIsNone(r["metrics"]["age_days"])
            self.assertIsNone(r["metrics"]["exposure_fraction"])
            self.assertIsNone(r["metrics"]["views_age_adjusted"])
            self.assertIsNone(r["metrics"]["downloads_age_adjusted"])
            self.assertIsNotNone(r["metrics"]["age_validation_error"])

    def test_validator_rejects_tampered_score_and_accepts_model_arithmetic(self):
        import copy
        spec = importlib.util.spec_from_file_location("library_validator", Path(__file__).resolve().parents[1] / "validate_library.py")
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        r = self.normalized(7)
        r["source"], r["id"], r["url"] = "psyarxiv", "psyarxiv:x", "https://osf.io/preprints/psyarxiv/x/"
        r["metrics"].update(versions=1, components=[{"source_id": "x", "views": 100, "downloads": 50, "published_at": r["first_posted_at"]}])
        data, _ = build_library([{"records": [r], "coverage": {"source": "psyarxiv", "status": "complete", "fetched": 1}}], "2026-09-12", "2026-10-12", exposure_tau_days=14)
        self.assertEqual(data["ranking"]["exposure_tau_days"], 14)
        self.assertEqual(validator.validate(data)["all_version_usage_verified"], 1)
        invalid = copy.deepcopy(data)
        invalid["papers"][0]["metrics"]["views_age_adjusted"] += 10
        with self.assertRaises(AssertionError):
            validator.validate(invalid)
        for observed in (None, "invalid", "2026-09-11T12:00:00Z"):
            invalid = copy.deepcopy(data)
            invalid["papers"][0]["metrics"]["observed_at"] = observed
            with self.assertRaises(AssertionError):
                validator.validate(invalid)


if __name__ == "__main__":
    unittest.main()
