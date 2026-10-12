"""All pages of selected bioRxiv subject categories, with first-version checks."""
from __future__ import annotations

import concurrent.futures
from urllib.parse import urlencode

from common import HTTPClient, FetchError, utc_now
from screen import screen

API = "https://api.biorxiv.org/details/biorxiv"
DEFAULT_CATEGORIES = ["neuroscience", "animal behavior and cognition", "bioinformatics", "systems biology"]


def normalize(item, meta):
    doi = item["doi"]
    return {"id": "biorxiv:" + doi, "source": "biorxiv", "source_id": doi,
            "title": item.get("title", "").strip(), "abstract": item.get("abstract", "").strip(),
            "authors": [x.strip() for x in item.get("authors", "").split(";") if x.strip()],
            "published": item.get("date", ""), "updated": item.get("date", ""),
            "doi": doi, "journal_doi": item.get("published") if item.get("published") != "NA" else None,
            "url": "https://www.biorxiv.org/content/" + doi, "version": int(item.get("version") or 1),
            "withdrawn": "withdrawn" in str(item.get("type", "")).lower(),
            "source_categories": [item.get("category")],
            "provenance": {"metadata_url": meta["url"], "fetched_at": meta["fetched_at"], "sha256": meta["sha256"],
                           "first_post_verified": int(item.get("version") or 1) == 1}}


def harvest(start, end, cache_dir, refresh=False, all_categories=False, overrides=None):
    client = HTTPClient(cache_dir, refresh=refresh, delay=0.4)
    categories = [None] if all_categories else DEFAULT_CATEGORIES
    overrides = overrides or {}
    records, intervals, failures = [], [], []

    def category_pages(category):
        found, cursor, total = [], 0, None
        try:
            while total is None or cursor < total:
                url = f"{API}/{start}/{end}/{cursor}"
                if category:
                    url += "?" + urlencode({"category": category})
                body, meta = client.json(url)
                message = body.get("messages", [{}])[0]
                if message.get("status") not in ("ok", "no papers found"):
                    raise FetchError(f"bioRxiv status {message}")
                batch = body.get("collection", [])
                total = int(message.get("total", 0))
                if not batch:
                    if cursor < total:
                        raise FetchError(f"Empty bioRxiv page {cursor}/{total}")
                    break
                found.extend(normalize(item, meta) for item in batch)
                cursor += len(batch)
                if cursor % 300 == 0 or cursor >= total:
                    print(f"biorxiv {category or 'all'}: {cursor}/{total}", flush=True)
            return found, {"category": category or "all", "fetched": len(found), "total": total or 0, "status": "complete"}, None
        except (FetchError, ValueError, KeyError) as exc:
            return found, {"category": category or "all", "fetched": len(found), "total": total, "status": "partial" if found else "failed"}, {"category": category or "all", "cursor": cursor, "error": str(exc)}

    # bioRxiv documents no single-connection restriction. Four streams retrieve
    # disjoint categories while sharing a conservative aggregate request delay.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for found, interval, failure in pool.map(category_pages, categories):
            records.extend(found)
            intervals.append(interval)
            if failure:
                failures.append(failure)
    by_doi = {}
    for record in records:
        by_doi.setdefault(record["doi"], []).append(record)
    # Only evidence-based shortlist records lacking v1 require DOI history.
    # This prevents a within-window revision of an older manuscript being
    # mistaken for a new preprint.
    for doi, versions in by_doi.items():
        included = overrides.get("biorxiv:" + doi, {}).get("action") == "include"
        if not any(x["version"] == 1 for x in versions) and (included or any(screen(x)["status"] == "retained" for x in versions)):
            try:
                body, meta = client.json(f"{API}/{doi}/na/json")
                history = [normalize(item, meta) for item in body.get("collection", [])]
                if not history or not any(x["version"] == 1 for x in history):
                    raise FetchError("DOI history lacks version 1")
                records.extend(history)
            except (FetchError, ValueError, KeyError) as exc:
                failures.append({"doi": doi, "stage": "first_post", "error": str(exc)})
    return {"records": records, "coverage": {
        "source": "biorxiv", "status": "complete" if not failures else ("partial" if records else "failed"),
        "fetched": len(records), "window": {"start": start, "end": end}, "source_categories": categories,
        "scope": "All paginated date-range records in the named subject categories; relevant papers assigned to other categories may be missed.",
        "intervals": intervals, "failures": failures, "requests": client.requests, "cache_hits": client.cache_hits,
        "checked_at": utc_now(), "metrics_status": "bioRxiv /usage supplies platform aggregates, not paper-level views/downloads; no aggregate assigned to a paper.",
    }}
