"""Validate the delivered metadata and all-version usage arithmetic offline."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from urllib.parse import urlparse
import argparse
import json
import math

ROOT = Path(__file__).resolve().parents[1]


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate(data, require_complete=False):
    papers = data["papers"]
    assert papers, "The delivered library must contain real selected papers."
    allowed = {item["id"] for item in data["categories"]}
    identifiers = set()
    start, end = data["window"]["start"], data["window"]["end"]
    ranking = data["ranking"]
    assert ranking["default"] == "exponential-adjusted-views", "The default ranking must use the selected exposure correction."
    assert ranking["model"] == "exponential-saturation", "Unknown exposure correction model."
    tau = ranking["exposure_tau_days"]
    assert type(tau) in (int, float) and math.isfinite(tau) and tau > 0, "Exposure tau must be positive and finite."
    assert ranking["minimum_age_days"] == 1, "The zero-age floor must be one day."
    measured = 0
    for paper in papers:
        assert paper["id"] not in identifiers, f"Duplicate paper: {paper['id']}"
        identifiers.add(paper["id"])
        assert start <= paper["published"][:10] <= end, f"First posting outside window: {paper['id']}"
        assert paper["title"].strip() and paper["abstract"].strip(), f"Missing requested metadata: {paper['id']}"
        assert paper["authors"] and all(isinstance(a, str) and a.strip() for a in paper["authors"]), f"Missing authors: {paper['id']}"
        assert not paper.get("_authors_pending"), f"Unresolved authors: {paper['id']}"
        assert paper["categories"] and set(paper["categories"]) <= allowed, f"Invalid reading area: {paper['id']}"
        assert paper["screening"]["status"] == "retained", f"Nonselected paper delivered: {paper['id']}"
        assert urlparse(paper["url"]).scheme == "https", f"Unsafe source URL: {paper['id']}"
        assert urlparse(paper["url"]).hostname in {"arxiv.org", "www.arxiv.org", "biorxiv.org", "www.biorxiv.org", "osf.io", "www.osf.io"}, f"Unrecognized original source: {paper['id']}"
        metrics = paper["metrics"]
        assert "views_per_day" not in metrics and "downloads_per_day" not in metrics, f"Legacy rate fields remain in delivered scores: {paper['id']}"
        for kind in ("views", "downloads"):
            count = metrics.get(kind)
            assert count is None or (type(count) in (int, float) and math.isfinite(count) and count >= 0), f"Invalid usage count: {paper['id']}"
        complete = metrics.get("coverage_complete") is True and metrics.get("scope") in {"all-versions", "sum_of_public_versions"}
        if complete:
            components = metrics.get("components", [])
            assert components, f"All-version total lacks evidence: {paper['id']}"
            versions = [c["source_id"] for c in components]
            assert len(versions) == len(set(versions)), f"Repeated version count: {paper['id']}"
            assert metrics["versions"] == len(components), f"Version total mismatch: {paper['id']}"
            for kind in ("views", "downloads"):
                assert metrics[kind] == sum(c[kind] for c in components), f"Incorrect all-version sum: {paper['id']}"
            first = metrics.get("first_posted_at") or metrics.get("first_post_date") or paper.get("first_posted_at") or paper["published"]
            assert metrics.get("observed_at"), f"All-version aggregate lacks its observation time: {paper['id']}"
            try:
                elapsed = (instant(metrics["observed_at"]) - instant(first)).total_seconds() / 86400
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                raise AssertionError(f"Invalid first-post/observation timestamp: {paper['id']}") from exc
            assert elapsed >= 0, f"Counter observation predates the first posting: {paper['id']}"
            assert metrics.get("age_validation_error") is None, f"Invalid age was used for a complete rank: {paper['id']}"
            age = max(1, elapsed)
            expected_basis = "first-post-timestamp" if "T" in first else "first-post-date-midnight-UTC"
            assert metrics["age_basis"] == expected_basis, f"Age timestamp precision is mislabeled: {paper['id']}"
            component_instants = [instant(c["published_at"]) for c in components if c.get("published_at")]
            if metrics.get("first_posted_at") and len(component_instants) == len(components):
                assert instant(metrics["first_posted_at"]) == min(component_instants), f"Score uses a later version's timestamp: {paper['id']}"
            fraction = -math.expm1(-age / tau)
            assert math.isclose(metrics["age_days"], age, rel_tol=1e-9, abs_tol=1e-9), f"Age differs from first-post observation interval: {paper['id']}"
            assert metrics["exposure_tau_days"] == tau, f"Paper uses a different exposure timescale: {paper['id']}"
            assert math.isclose(metrics["exposure_fraction"], fraction, rel_tol=1e-9, abs_tol=0), f"Exposure fraction differs from the selected model: {paper['id']}"
            for kind in ("views", "downloads"):
                score = metrics[f"{kind}_age_adjusted"]
                assert type(score) in (int, float) and math.isfinite(score), f"Invalid adjusted score: {paper['id']}"
                assert math.isclose(score, metrics[kind] / fraction, rel_tol=1e-9, abs_tol=1e-9), f"Exposure-adjusted score differs from the selected formula: {paper['id']}"
            measured += 1
        else:
            assert metrics.get("views_age_adjusted") is None and metrics.get("downloads_age_adjusted") is None, f"Incomplete total assigned an adjusted score: {paper['id']}"
    if require_complete:
        assert {c["source"] for c in data["coverage"]} == {"arxiv", "psyarxiv", "biorxiv"}, "All three source adapters must be represented."
        assert all(c["status"] == "complete" for c in data["coverage"]), "A source crawl remains incomplete."
    return {"papers": len(papers), "sources": dict(Counter(p["source"] for p in papers)), "all_version_usage_verified": measured, "window": data["window"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=ROOT / "library/data.json")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate(json.loads(args.path.read_text(encoding="utf-8")), args.require_complete), ensure_ascii=False))
