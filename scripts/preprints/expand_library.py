"""Resume a local year backfill in monthly batches; never publish the site.

The current-month corpora stay separate from archived history. Failed or missing
months remain explicit gaps, and all saved batches survive the next daily run.
"""
from __future__ import annotations

import argparse
import calendar
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from common import utc_now, write_json
from update_library import ROOT, SOURCES, build_library, main as update

DATA = ROOT / "data/preprints"
ARCHIVE = DATA / "backfill"


def month_before(value):
    year, month = value.year, value.month - 1
    if month == 0:
        year, month = year - 1, 12
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def year_before(value):
    return date(value.year - 1, value.month,
                min(value.day, calendar.monthrange(value.year - 1, value.month)[1]))


def uncovered(start, end, intervals):
    """Inclusive missing spans, with overlaps and adjacent spans consolidated."""
    cursor, gaps = start, []
    for lo, hi in sorted(intervals):
        lo, hi = max(start, lo), min(end, hi)
        if hi < cursor or lo > end:
            continue
        if lo > cursor:
            gaps.append((cursor, lo - timedelta(days=1)))
        cursor = max(cursor, hi + timedelta(days=1))
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def seed_current():
    # Preserve the complete current-month snapshot even when future updates
    # move the rolling month boundary. Never overwrite a historical slot.
    windows = [read(DATA / f"{source}.json")["coverage"].get("window")
               for source in SOURCES if (DATA / f"{source}.json").exists()]
    if not windows or any(window != windows[0] for window in windows):
        raise ValueError("Current source snapshots need a common dated window.")
    window = windows[0]
    folder = ARCHIVE / (window["start"] + "_" + window["end"])
    folder.mkdir(parents=True, exist_ok=True)
    for source in SOURCES:
        path = DATA / f"{source}.json"
        if path.exists():
            shutil.copy2(path, folder / path.name)


def collect(start, end):
    results, all_gaps = [], []
    for source in SOURCES:
        records, segments, intervals = [], [], []
        authoritative_checks, excluded_checks = [], {}
        snapshots = [(path, read(path)) for path in ARCHIVE.glob(f"*/{source}.json")]
        def checked(item):
            value = item[1]["coverage"].get("checked_at")
            if not value:
                return datetime.min.replace(tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        # A successful later snapshot is authoritative for its first-post
        # interval. Older archives must not resurrect removed/withdrawn works.
        for path, snapshot in sorted(snapshots, key=checked):
            coverage = snapshot["coverage"]
            window = coverage.get("window", {})
            if not window.get("start") or not window.get("end"):
                continue
            lo, hi = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
            if hi < start or lo > end:
                continue
            if coverage.get("status") == "complete":
                records = [record for record in records
                           if not str(lo) <= record.get("published", "")[:10] <= str(hi)]
            excluded = set(coverage.get("excluded_ids", []))
            for identifier in excluded:
                excluded_checks[identifier] = checked((path, snapshot))
            records = [record for record in records if record["id"] not in excluded]
            # A narrow refresh can carry older records in its saved corpus.
            # Keep unique revision-history evidence, but do not let copied
            # stale metadata undo a newer removal or withdrawal.
            previous_check = coverage.get("previous_checked_at")
            for record in snapshot["records"]:
                if record["id"] in excluded:
                    continue
                in_batch = str(lo) <= record.get("published", "")[:10] <= str(hi)
                fetched_at = record.get("provenance", {}).get("fetched_at")
                if not in_batch and previous_check:
                    if not fetched_at:
                        continue
                    fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    previous = datetime.fromisoformat(previous_check.replace("Z", "+00:00"))
                    if fetched <= previous:
                        # Cached DOI histories can be the only evidence for an
                        # older first post whose revision falls in this batch.
                        # Reject carryover only if a newer authoritative check
                        # actually covered that first-post date (or excluded ID).
                        first_post = record.get("published", "")[:10]
                        removed_by_window = any(str(a) <= first_post <= str(b) and observed >= fetched
                                                for a, b, observed in authoritative_checks)
                        removed_by_id = excluded_checks.get(record["id"], datetime.min.replace(tzinfo=timezone.utc)) >= fetched
                        if removed_by_window or removed_by_id:
                            continue
                records.append(record)
            if coverage.get("status") == "complete":
                authoritative_checks.append((lo, hi, checked((path, snapshot))))
            segments.append({**coverage, "archive": str(path.relative_to(ROOT))})
            usage_pending = (coverage.get("metrics_error") or
                             coverage.get("metrics_enrichment", {}).get("failures") or
                             coverage.get("metrics_enrichment", {}).get("status") == "in_progress")
            if coverage.get("status") == "complete" and not usage_pending:
                intervals.append((lo, hi))
        gaps = uncovered(start, end, intervals)
        all_gaps.extend(gaps)
        unresolved = [segment for segment in segments if any(
            date.fromisoformat(segment["window"]["start"]) <= hi and
            date.fromisoformat(segment["window"]["end"]) >= lo for lo, hi in gaps)]
        failures = [failure for segment in unresolved for failure in segment.get("failures", [])]
        metric_failures = [failure for segment in unresolved
                           for failure in segment.get("metrics_enrichment", {}).get("failures", [])]
        coverage = {"source": source, "status": "partial" if gaps or failures else "complete",
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "checked_at": utc_now(), "fetched": len(records),
                    "retrieval_scope": "Progressive year backfill using the existing source queries/categories; archived snapshots retain their observation dates.",
                    "scope": "Monthly archive snapshots; historical expansion in progress." if gaps else "Completed dated batches for the requested year; source query/category limits still apply.",
                    "missing_windows": [{"start": lo.isoformat(), "end": hi.isoformat()} for lo, hi in gaps],
                    "batch_snapshots": segments, "failures": failures,
                    "metrics_enrichment": {"failures": metric_failures},
                    "metrics_status": "Version totals retain the actual observation dates; missing counters are unavailable."}
        if gaps:
            coverage["scope"] += " Still missing: " + "; ".join(f"{lo} to {hi}" for lo, hi in gaps)
        results.append({"records": records, "coverage": coverage})
    return results, all_gaps


def rebuild(start, end):
    results, gaps = collect(start, end)
    overrides = read(DATA / "overrides.json") if (DATA / "overrides.json").exists() else {}
    library, audits = build_library(results, start.isoformat(), end.isoformat(), overrides)
    library["backfill"] = {"target": "past calendar year", "status": "in_progress" if gaps else "complete",
                           "batch_size": "one calendar month", "archive_dir": "data/preprints/backfill"}
    # Validate before replacing the user-facing artifact.
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_library import validate
    report = validate(library)
    write_json(DATA / "audit.json", {"generated_at": library["generated_at"], "window": library["window"],
                                    "counts": library["screening"]["audit_counts"], "records": audits})
    write_json(DATA / "manifest.json", {k: v for k, v in library.items() if k != "papers"})
    write_json(ROOT / "library/data.json", library)
    write_json(DATA / "backfill-state.json", {"updated_at": utc_now(), "window": library["window"],
                                             "status": library["backfill"]["status"], "coverage": library["coverage"],
                                             "report": report})
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return results, gaps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--rebuild-only", action="store_true")
    args = parser.parse_args(argv)
    if args.batch_count < 1:
        parser.error("--batch-count must be positive")
    end = date.fromisoformat(args.end)
    start = year_before(end)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    seed_current()
    results, gaps = rebuild(start, end)
    if args.rebuild_only:
        return 0
    for _ in range(args.batch_count):
        if not gaps:
            print("All requested year metadata intervals are covered.", flush=True)
            break
        # Fill the newest uncovered span first; retry a failed span next run.
        lo, hi = max(gaps, key=lambda gap: gap[1])
        lo = max(lo, month_before(hi + timedelta(days=1)))
        folder = ARCHIVE / f"{lo}_{hi}"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DATA / "overrides.json", folder / "overrides.json")
        print(f"Backfill batch: {lo} through {hi}", flush=True)
        status = update(["--start", str(lo), "--end", str(hi), "--full", "--use-cache", "--strict",
                         "--cache-dir", str(DATA / "cache"), "--data-dir", str(folder),
                         "--output", str(folder / "selected.json"), "--exposure-tau-days", "7"])
        results, gaps = rebuild(start, end)
        if status:
            print("Batch remains incomplete; saved cache and explicit gaps allow retry.", flush=True)
            return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
