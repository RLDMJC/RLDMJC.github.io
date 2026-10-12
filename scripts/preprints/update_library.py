"""Build an unpublished metadata library with reproducible source and triage audit.

Run from anywhere: python scripts/preprints/update_library.py --start YYYY-MM-DD
--end YYYY-MM-DD. Default: refresh the complete rolling 30-day window. Use
--use-cache to replay raw pages, or --offline to rebuild saved source corpora.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from common import utc_now, write_json
from screen import CATEGORIES, RULE_VERSION, screen

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ["arxiv", "psyarxiv", "biorxiv"]
NULL_METRICS = {"views": None, "downloads": None, "observed_at": None, "source_url": None}
DEFAULT_EXPOSURE_TAU_DAYS = 7.0


def positive_finite(value):
    """argparse/direct-call validation for the configurable heuristic timescale."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("Exposure tau must be a positive finite number of days.") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Exposure tau must be a positive finite number of days.")
    return result


def exposure_fraction(age_days, tau_days=DEFAULT_EXPOSURE_TAU_DAYS):
    tau_days = positive_finite(tau_days)
    # expm1 avoids subtracting two almost equal floating-point values for young
    # papers or large tau. The one-day floor prevents a zero-age singularity.
    return -math.expm1(-max(1.0, age_days) / tau_days)


def normalized_title(title):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower())


def normalized_doi(doi):
    if not doi or doi == "NA":
        return None
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.I).lower()


def normalize_record(record):
    record = dict(record)
    record["source_id"] = record.get("source_id") or record.get("sourceId") or record["id"].split(":", 1)[-1]
    record["published"] = (record.get("published") or record.get("firstPosted") or "")[:10]
    record["updated"] = record.get("updated") or record["published"]
    record["doi"] = normalized_doi(record.get("doi"))
    record["metrics"] = {**NULL_METRICS, **(record.get("metrics") or {})}
    record["metrics"]["observed_at"] = record["metrics"].get("observed_at") or record["metrics"].get("asOf")
    record["provenance"] = record.get("provenance") or {}
    return record


def dedupe_versions(records):
    groups = {}
    for raw in records:
        record = normalize_record(raw)
        stable_id = record["id"] if record["source"] == "psyarxiv" else record["source_id"]
        groups.setdefault((record["source"], stable_id), []).append(record)
    result = []
    for versions in groups.values():
        latest = dict(max(enumerate(versions), key=lambda pair: (str(pair[1].get("updated") or ""), int(pair[1].get("version") or 1), str(pair[1].get("provenance", {}).get("fetched_at") or ""), pair[0]))[1])
        first = min(x["published"] for x in versions if x.get("published"))
        latest["published"] = first
        latest["provenance"] = dict(latest["provenance"])
        latest["provenance"]["first_post_verified"] = any(x["provenance"].get("first_post_verified") or int(x.get("version") or 1) == 1 for x in versions)
        result.append(latest)
    return result


def author_overlap(a, b):
    def names(record):
        result = set()
        for name in record.get("authors", []):
            name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
            if "," in name:
                family, given = name.split(",", 1)
            else:
                tokens = name.split()
                family, given = (tokens[-1], " ".join(tokens[:-1])) if len(tokens) > 1 else (name, "")
            family = re.sub(r"[^a-z]", "", family)
            given = re.sub(r"[^a-z]", "", given)
            if family:
                result.add(family + ":" + (given[:1] if given else ""))
        return result
    return bool(names(a) & names(b))


def dedupe_cross_sources(records):
    groups, doi_index, title_index = [], {}, {}
    for record in sorted(records, key=lambda x: (x["published"], x["source"], x["id"])):
        doi, title = normalized_doi(record.get("doi")), normalized_title(record["title"])
        index = doi_index.get(doi) if doi else None
        if index is None and len(title) >= 20:
            for candidate in title_index.get(title, []):
                if groups[candidate][0]["source"] != record["source"] and author_overlap(groups[candidate][0], record):
                    index = candidate
                    break
        if index is None:
            index = len(groups)
            groups.append([])
        groups[index].append(record)
        if doi:
            doi_index[doi] = index
        title_index.setdefault(title, []).append(index)
    merged = []
    for members in groups:
        primary = dict(max(members, key=lambda x: (x.get("updated", ""), x.get("provenance", {}).get("fetched_at", ""))))
        primary["id"] = members[0]["id"]
        primary["published"] = min(x["published"] for x in members)
        primary["aliases"] = [{"id": x["id"], "source": x["source"], "url": x["url"], "published": x["published"]} for x in members]
        primary["duplicate_count"] = len(members) - 1
        primary["categories"] = list(dict.fromkeys(value for x in members for value in x.get("categories", [])))
        primary["tags"] = list(dict.fromkeys(value for x in members for value in x.get("tags", [])))
        # Preserve provenance from every source; do not mix incomparable counters.
        primary["source_records"] = [{"source": x["source"], "id": x["id"], "doi": x.get("doi"), "provenance": x["provenance"], "metrics": x["metrics"]} for x in members]
        with_metrics = [x for x in members if x["metrics"].get("views") is not None or x["metrics"].get("downloads") is not None]
        if with_metrics:
            primary["metrics"] = dict(with_metrics[0]["metrics"])
            primary["metrics"]["preprint_source"] = with_metrics[0]["source"]
            primary["metrics"]["first_post_date"] = with_metrics[0]["published"]
        merged.append(primary)
    return merged


def within_window(paper, start, end):
    return start <= paper.get("published", "") <= end


def normalized_metrics(paper, end, exposure_tau_days=DEFAULT_EXPOSURE_TAU_DAYS):
    exposure_tau_days = positive_finite(exposure_tau_days)
    metrics = dict(paper["metrics"])
    metrics.pop("views_per_day", None)
    metrics.pop("downloads_per_day", None)
    # Use the counter's observation date and associated first preprint date.
    observed = metrics.get("observed_at")
    posted = metrics.get("first_posted_at") or metrics.get("first_post_date") or paper.get("first_posted_at") or paper["published"]
    age, invalid_age = None, None
    if not observed:
        invalid_age = "missing-observation-time"
    else:
        try:
            observation_time = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if observation_time.tzinfo is None:
                observation_time = observation_time.replace(tzinfo=timezone.utc)
            publication_time = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            if publication_time.tzinfo is None:
                publication_time = publication_time.replace(tzinfo=timezone.utc)
            elapsed = (observation_time - publication_time).total_seconds() / 86400
            if elapsed < 0:
                invalid_age = "observation-before-first-post"
            else:
                age = max(1, elapsed)
        except (ValueError, TypeError, AttributeError, OverflowError):
            invalid_age = "invalid-time"
    paper["age_days"] = age
    comparable = metrics.get("scope") in ("all-versions", "sum_of_public_versions") and metrics.get("coverage_complete") is True
    fraction = exposure_fraction(age, exposure_tau_days) if age is not None else None

    def adjusted(kind):
        count = metrics.get(kind)
        if not comparable or fraction is None or type(count) not in (int, float) or not math.isfinite(count) or count < 0:
            return None
        score = count / fraction
        if not math.isfinite(score):
            raise ValueError("Exposure-adjusted count exceeds finite numeric range; choose a smaller --exposure-tau-days.")
        return score

    paper["metrics"] = {**metrics, "age_days": age,
                        "age_basis": "first-post-timestamp" if isinstance(posted, str) and "T" in posted else "first-post-date-midnight-UTC",
                        "age_validation_error": invalid_age,
                        "exposure_fraction": fraction, "exposure_tau_days": exposure_tau_days,
                        "views_age_adjusted": adjusted("views"),
                        "downloads_age_adjusted": adjusted("downloads")}


def build_library(results, start, end, overrides=None, exposure_tau_days=DEFAULT_EXPOSURE_TAU_DAYS):
    exposure_tau_days = positive_finite(exposure_tau_days)
    overrides = overrides or {}
    versions = dedupe_versions([record for item in results for record in item["records"]])
    audits, shortlisted = [], []
    for record in versions:
        assessment = screen(record)
        override = overrides.get(record["id"])
        if override:
            if override.get("action") == "exclude":
                assessment.update(status="rejected", reason=override.get("reason", "Editorial exclusion."))
            elif override.get("action") == "include":
                assessment.update(status="retained", reason=override.get("reason", "Editorial inclusion."),
                                  categories=override.get("categories") or assessment["categories"])
            assessment["editorial_override"] = override
        in_window = within_window(record, start, end)
        verified = record["provenance"].get("first_post_verified", False)
        if assessment["status"] == "retained" and not verified:
            assessment.update(status="candidate", reason="First-post date is unverified; retained in audit until source history can be checked.")
        if assessment["status"] == "retained" and (record.get("_authors_pending") or not record.get("authors")):
            assessment.update(status="candidate", reason="Full bibliographic authors have not been retrieved; withheld from the review library until source enrichment succeeds.")
        if not in_window:
            assessment.update(status="outside-window", reason="First preprint date falls outside the requested inclusive date window.")
        if record.get("withdrawn"):
            assessment.update(status="rejected", reason="Source marks the latest manuscript as withdrawn; retained in provenance audit only.")
        record["categories"], record["tags"] = assessment.pop("categories"), assessment.pop("tags")
        record["screening"] = {**assessment, "method": "title-and-abstract rules", "human_reviewed": bool(override and override.get("reviewer") == "user"),
                               "editorial_reviewed": bool(override), "editorial_reviewer": override.get("reviewer", "codex") if override else None}
        audits.append(record)
        if assessment["status"] == "retained":
            shortlisted.append(record)
    papers = dedupe_cross_sources(shortlisted)
    for paper in papers:
        normalized_metrics(paper, end, exposure_tau_days)
        for internal in ("_versions", "_authors_pending", "sourceId", "firstPosted"):
            paper.pop(internal, None)
    papers.sort(key=lambda p: (p["published"], p["title"]), reverse=True)
    coverage = []
    for result in results:
        item = dict(result["coverage"])
        source = item["source"]
        item.setdefault("fetched", item.get("retrieved_version_records", len(result["records"])))
        item.setdefault("window", {"start": item.get("start", start), "end": item.get("end", end)})
        item.setdefault("checked_at", item.get("observed_to"))
        counts = Counter(p["screening"]["status"] for p in audits if p["source"] == source)
        item.update(unique_records=sum(counts.values()), retained=counts["retained"], candidates=counts["candidate"], rejected=counts["rejected"], outside_window=counts["outside-window"])
        coverage.append(item)
    measured = sum(p["metrics"].get("views_age_adjusted") is not None or p["metrics"].get("downloads_age_adjusted") is not None for p in papers)
    library = {"schema_version": 2, "generated_at": utc_now(), "window": {"start": start, "end": end},
               "coverage": coverage, "categories": CATEGORIES, "papers": papers,
               "screening": {"rule_version": RULE_VERSION, "method": "Conservative, reproducible title/abstract triage with optional persistent editorial overrides.",
                             "human_reviewed": False, "audit_counts": dict(Counter(x["screening"]["status"] for x in audits)),
                             "limitations": "Automatic selection can miss relevant studies and include keyword matches; scientific novelty, validity, and speaker suitability require reading the paper."},
               "ranking": {"requested_default": "exponential-adjusted-views", "default": "exponential-adjusted-views", "fallback": "newest",
                           "measured_papers": measured, "missing_metrics": len(papers) - measured,
                           "model": "exponential-saturation", "exposure_tau_days": exposure_tau_days, "minimum_age_days": 1,
                           "parameter_basis": "Heuristic configurable timescale; not empirically fitted to usage data.",
                           "formula": "total_count / (-expm1(-age_days / exposure_tau_days)), where age_days=max(1, elapsed UTC days from first posting to the counter observation).",
                           "age_precision": "Prefer the verified source-specific first-post timestamp; when only a date is available use midnight UTC and label age_basis accordingly.",
                           "interpretation": "A model-based exposure correction of cumulative counts. Older papers approach their raw totals; younger papers receive a saturating exposure correction. This is not a measured final count or a per-day usage rate.",
                           "comparability": "Source counters have different definitions. Rank measured papers within a source; missing counters are null and are never inferred from citations or platform totals."}}
    return library, audits


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--sources", nargs="+", choices=SOURCES, default=SOURCES)
    parser.add_argument("--refresh", dest="refresh", action="store_true", default=True, help="Refresh all queried metadata and usage counters (default).")
    parser.add_argument("--use-cache", dest="refresh", action="store_false", help="Replay cached HTTP pages; their observation dates may be stale.")
    parser.add_argument("--full", action="store_true", help="Query the full date window even with existing complete source corpora.")
    parser.add_argument("--incremental", action="store_true", help="Opt in to a first-post overlap update; older revisions can remain stale. Full refresh is the default.")
    parser.add_argument("--window-days", type=int, default=30, help="Rolling first-post window when --start is omitted.")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--exposure-tau-days", type=positive_finite, default=DEFAULT_EXPOSURE_TAU_DAYS,
                        help="Positive finite heuristic timescale for exponential-saturation exposure correction (default: 7 days; not an empirical fit).")
    parser.add_argument("--biorxiv-all", action="store_true", help="Fetch every bioRxiv category; substantially more API calls.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if a source or requested usage enrichment fails; still write an honest review artifact.")
    parser.add_argument("--offline", action="store_true", help="Rebuild from the last locally saved source metadata without network calls.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/preprints")
    parser.add_argument("--cache-dir", type=Path, help="Shared raw HTTP cache for separately staged date batches.")
    parser.add_argument("--output", type=Path, default=ROOT / "library/data.json")
    args = parser.parse_args(argv)
    end = date.fromisoformat(args.end)
    start = args.start or (end - timedelta(days=args.window_days)).isoformat()
    if date.fromisoformat(start) > end:
        parser.error("--start must be on or before --end")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.cache_dir or args.data_dir / "cache"
    overrides_path = args.data_dir / "overrides.json"
    if not overrides_path.exists():
        write_json(overrides_path, {})
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))

    def fetch_source(source):
        corpus_path = args.data_dir / f"{source}.json"
        prior = json.loads(corpus_path.read_text(encoding="utf-8")) if corpus_path.exists() else None
        if args.offline:
            if prior:
                return prior
            return {"records": [], "coverage": {"source": source, "status": "failed", "fetched": 0, "failures": [{"error": "No local source corpus for offline rebuild."}]}}
        prior_window = (prior or {}).get("coverage", {}).get("window", {})
        incremental = (args.incremental and prior and prior["coverage"].get("status") == "complete" and not args.full
                       and prior_window.get("start", "9999") <= start
                       and prior_window.get("end", "") >= (end - timedelta(days=args.lookback_days)).isoformat())
        query_start = max(start, (end - timedelta(days=args.lookback_days)).isoformat()) if incremental else start
        try:
            module = importlib.import_module(source)
            options = {"all_categories": args.biorxiv_all, "overrides": overrides} if source == "biorxiv" else {}
            fresh = module.harvest(query_start, args.end, cache_dir / source, refresh=args.refresh, **options)
        except Exception as exc:
            fresh = {"records": [], "coverage": {"source": source, "status": "failed", "fetched": 0, "failures": [{"error": f"{type(exc).__name__}: {exc}"}], "checked_at": utc_now()}}
        if prior:
            previous = prior["records"]
            if fresh["coverage"].get("status") == "complete" and not incremental:
                # A complete fresh window is authoritative. Never resurrect a
                # withdrawn/deleted or no-longer-matching work merely because
                # an older snapshot contained it.
                previous = [r for r in previous if not within_window(r, start, args.end)]
            excluded = set(fresh["coverage"].get("excluded_ids", []))
            previous = [r for r in previous if r["id"] not in excluded]
            fresh["records"] = previous + fresh["records"]
            fresh["coverage"]["preserved_previous_records"] = len(previous)
            fresh["coverage"]["previous_checked_at"] = prior["coverage"].get("checked_at")
        fresh["coverage"]["window"] = {"start": start, "end": args.end}
        fresh["coverage"]["update_window"] = {"start": query_start, "end": args.end}
        fresh["coverage"]["incremental"] = bool(incremental)
        if incremental:
            fresh["coverage"]["revision_refresh"] = "partial; only new posts/revisions returned within the requested overlap are refreshed"
        fresh["records"] = dedupe_versions(fresh["records"])
        write_json(corpus_path, fresh)
        print(f"{source}: {fresh['coverage']['status']}, {len(fresh['records'])} unique source papers", flush=True)
        return fresh

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fetched = list(pool.map(fetch_source, args.sources))
    # Sources excluded from this invocation remain represented by saved metadata.
    for source in SOURCES:
        if source not in args.sources and (args.data_dir / f"{source}.json").exists():
            fetched.append(json.loads((args.data_dir / f"{source}.json").read_text(encoding="utf-8")))
    # PsyArXiv is the only adapter with verified per-paper counters. Enrich just
    # the shortlist, and preserve prior counters if a metrics request fails.
    for result in fetched:
        if result["coverage"]["source"] == "psyarxiv" and "psyarxiv" in args.sources and not args.offline:
            try:
                module = importlib.import_module("psyarxiv")
                selected = [r for r in result["records"] if within_window(r, start, args.end)
                            and (screen(r)["status"] == "retained" or overrides.get(r["id"], {}).get("action") == "include")
                            and overrides.get(r["id"], {}).get("action") != "exclude"]
                summary = module.enrich_metrics(selected, cache_dir / "psyarxiv", refresh=args.refresh)
                result["coverage"]["metrics_enrichment"] = summary
                write_json(args.data_dir / "psyarxiv.json", result)
            except Exception as exc:
                result["coverage"]["metrics_error"] = f"{type(exc).__name__}: {exc}"
    library, audits = build_library(fetched, start, args.end, overrides, args.exposure_tau_days)
    write_json(args.data_dir / "audit.json", {"generated_at": library["generated_at"], "window": library["window"], "rule_version": RULE_VERSION, "counts": library["screening"]["audit_counts"], "records": audits})
    write_json(args.data_dir / "manifest.json", {k: v for k, v in library.items() if k != "papers"})
    write_json(args.output, library)
    print(json.dumps({"papers": len(library["papers"]), "audit": library["screening"]["audit_counts"], "metrics": library["ranking"]["measured_papers"], "output": str(args.output)}, ensure_ascii=False), flush=True)
    incomplete = any(x["status"] != "complete" or x.get("metrics_error")
                     or x.get("metrics_enrichment", {}).get("failures")
                     or x.get("metrics_enrichment", {}).get("status") == "in_progress"
                     for x in library["coverage"])
    return int(args.strict and incomplete)


if __name__ == "__main__":
    sys.exit(main())
