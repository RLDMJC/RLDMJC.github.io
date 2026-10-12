"""Public PsyArXiv metadata and usage metrics through the official OSF API.

No authentication, scraping, PDFs, or invented popularity proxies are used.
OSF returns versioned resources. The date filter is applied to version publish
dates, then only works whose public v1 was first posted in the requested window
are retained. Metrics are enriched separately, after topic screening.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen

API = "https://api.osf.io/v2/"
API_VERSION = "2.20"
USER_AGENT = "RLDMJC-PreprintLibrary/1.0 (+https://rldmjc.github.io/)"
PREPRINT_FIELDS = (
    "title,description,date_published,date_modified,date_created,version,"
    "is_latest_version,date_withdrawn,doi,tags,bibliographic_contributors,provider"
)
CONTRIBUTOR_FIELDS = "index,bibliographic,unregistered_contributor,users"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _collection_total(response: dict) -> int | None:
    # API versions >=2.3 move pagination metadata from links.meta to meta.
    return response.get("meta", {}).get("total", response.get("links", {}).get("meta", {}).get("total"))


def _save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


class OSFClient:
    """Sequential requests with bounded retry, explicit provenance, and disk cache."""

    def __init__(self, cache_dir: Path, refresh: bool = False, timeout: int = 75):
        self.cache_dir = Path(cache_dir)
        self.refresh = refresh
        self.timeout = timeout
        self.last_request = 0.0
        self.pace_lock = threading.Lock()
        self.requests = 0
        self.cached = 0
        self.failures: list[dict] = []
        self.rich_pages: dict[str, Path] = {}
        if not refresh and self.cache_dir.exists():
            for path in self.cache_dir.glob("psyarxiv-*.json"):
                saved = json.loads(path.read_text(encoding="utf-8"))
                parsed = urlparse(saved.get("url", ""))
                params = parse_qsl(parsed.query)
                if parsed.path == "/v2/preprints/" and ("embed", "bibliographic_contributors") in params:
                    key = parsed._replace(query=urlencode(sorted((key, value) for key, value in params if key != "embed"))).geturl()
                    self.rich_pages[key] = path

    def get(self, url: str) -> tuple[dict, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.osf.io":
            raise ValueError("Refusing an unexpected pagination host: " + url)
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        path = self.cache_dir / ("psyarxiv-" + key + ".json")
        if path.exists() and not self.refresh:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("url") != url or not isinstance(saved.get("response"), dict):
                raise ValueError("Invalid cached OSF response: " + str(path))
            self.cached += 1
            return saved["response"], saved["fetched_at"]
        projected_key = parsed._replace(query=urlencode(sorted(parse_qsl(parsed.query)))).geturl()
        if projected_key in self.rich_pages:
            saved = json.loads(self.rich_pages[projected_key].read_text(encoding="utf-8"))
            # An already cached richer representation satisfies the same filter
            # and pagination request without repeating it. Drop embed from next
            # links so uncached pages use the faster metadata-only representation.
            response = saved["response"]
            for key in ("self", "next", "prev", "first", "last"):
                link = response.get("links", {}).get(key)
                if link:
                    link_parts = urlparse(link)
                    response["links"][key] = link_parts._replace(query=urlencode([(key, value) for key, value in parse_qsl(link_parts.query) if key != "embed"])).geturl()
            self.cached += 1
            return response, saved["fetched_at"]
        last_error: Exception | None = None
        for attempt in range(5):
            # Shared by enrichment workers: at most one new request per second
            # across all in-flight work, while slow independent reads can overlap.
            with self.pace_lock:
                wait = 1.0 - (time.monotonic() - self.last_request)
                if wait > 0:
                    time.sleep(wait)
                self.last_request = time.monotonic()
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.api+json"})
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if payload.get("errors"):
                    raise ValueError("OSF returned JSON API errors: " + json.dumps(payload["errors"]))
                observed = _now()
                _save(path, {"url": url, "fetched_at": observed, "response": payload})
                self.requests += 1
                return payload, observed
            except HTTPError as error:
                last_error = error
                if error.code not in (408, 429, 500, 502, 503, 504):
                    break
                retry_after = error.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else min(45, 3 * (2 ** attempt))
                time.sleep(min(delay, 60))
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                time.sleep(min(45, 3 * (2 ** attempt)))
        self.failures.append({"url": url, "error": str(last_error)})
        raise RuntimeError("OSF request failed after bounded retries: " + url + ": " + str(last_error))

    def collection(self, url: str) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        observations: list[str] = []
        seen_pages: set[str] = set()
        expected: int | None = None
        while url:
            if url in seen_pages:
                raise RuntimeError("OSF pagination loop: " + url)
            seen_pages.add(url)
            response, observed = self.get(url)
            data = response.get("data")
            if not isinstance(data, list):
                raise RuntimeError("Expected OSF JSON API collection at " + url)
            page_total = _collection_total(response)
            if expected is None:
                expected = page_total
            elif expected != page_total:
                raise RuntimeError("OSF collection changed during pagination; rerun with --refresh")
            rows.extend(data)
            observations.append(observed)
            url = response.get("links", {}).get("next")
        if expected is not None and len(rows) != expected:
            raise RuntimeError(f"Incomplete OSF pagination: expected {expected}, received {len(rows)}")
        if len({row.get("id") for row in rows}) != len(rows):
            raise RuntimeError("Duplicate OSF records across pages; rerun with --refresh")
        return rows, observations


def _related(resource: dict, relation: str) -> str | None:
    return resource.get("relationships", {}).get(relation, {}).get("links", {}).get("related", {}).get("href")


def _authors(resource: dict, client: OSFClient) -> list[str]:
    embedded = resource.get("embeds", {}).get("bibliographic_contributors")
    contributors: list[dict] = []
    if embedded:
        contributors.extend(embedded.get("data", []))
        following = embedded.get("links", {}).get("next")
        if following:
            related = _related(resource, "bibliographic_contributors")
            if not related:
                raise RuntimeError("Missing paginated author link: " + resource["id"])
            separator = "&" if "?" in related else "?"
            url = related + separator + urlencode({"embed": "users", "fields[users]": "full_name", "fields[contributors]": CONTRIBUTOR_FIELDS, "page[size]": 100, "version": API_VERSION})
            contributors, _ = client.collection(url)
        total = _collection_total(embedded)
        if total is not None and total != len(contributors):
            raise RuntimeError("Incomplete embedded author list for " + resource["id"])
    else:
        related = _related(resource, "bibliographic_contributors")
        if not related:
            raise RuntimeError("Missing bibliographic contributor link: " + resource["id"])
        separator = "&" if "?" in related else "?"
        url = related + separator + urlencode({"embed": "users", "fields[users]": "full_name", "fields[contributors]": CONTRIBUTOR_FIELDS, "page[size]": 100, "version": API_VERSION})
        contributors, _ = client.collection(url)
    names: list[str] = []
    for contributor in sorted(contributors, key=lambda row: row.get("attributes", {}).get("index", 0)):
        attributes = contributor.get("attributes", {})
        if attributes.get("bibliographic") is False:
            continue
        name = attributes.get("unregistered_contributor")
        user = contributor.get("embeds", {}).get("users", {}).get("data", {})
        if not name:
            name = user.get("attributes", {}).get("full_name")
        if not name:
            related = _related(contributor, "users")
            if related:
                separator = "&" if "?" in related else "?"
                response, _ = client.get(related + separator + urlencode({"fields[users]": "full_name", "version": API_VERSION}))
                name = response.get("data", {}).get("attributes", {}).get("full_name")
        if not name:
            raise RuntimeError("Missing author name for " + contributor.get("id", resource["id"]))
        names.append(name.strip())
    if not names:
        raise RuntimeError("Empty author list for " + resource["id"])
    return names


def _version(resource: dict) -> int:
    number = resource.get("attributes", {}).get("version")
    if number is not None:
        return int(number)
    match = re.search(r"_v(\d+)$", resource["id"])
    return int(match.group(1)) if match else 1


def _withdrawn_ids(raw_rows: list[dict]) -> list[str]:
    latest: dict[str, dict] = {}
    for resource in raw_rows:
        base = resource["id"].split("_v")[0]
        if base not in latest or _version(resource) > _version(latest[base]):
            latest[base] = resource
    return sorted("psyarxiv:" + base for base, resource in latest.items() if resource.get("attributes", {}).get("date_withdrawn"))


def _normalize_resources(raw_rows: list[dict], client: OSFClient, metadata_url: str) -> tuple[list[dict], int, int]:
    by_work: dict[str, list[dict]] = {}
    for resource in raw_rows:
        base = resource["id"].split("_v")[0]
        by_work.setdefault(base, []).append(resource)
    records: list[dict] = []
    older_revisions = 0
    withdrawn = 0
    for base, versions in by_work.items():
        first = next((row for row in versions if _version(row) == 1), None)
        if first is None:
            # A recent revision of an older first post does not qualify.
            older_revisions += 1
            continue
        latest = max(versions, key=_version)
        attrs = latest["attributes"]
        if attrs.get("date_withdrawn"):
            withdrawn += 1
        first_posted = first["attributes"]["date_published"][:10]
        doi_url = latest.get("links", {}).get("preprint_doi")
        doi = doi_url.removeprefix("https://doi.org/").removeprefix("https://dx.doi.org/") if doi_url else None
        records.append({
            "id": "psyarxiv:" + base,
            "source": "psyarxiv",
            "source_id": latest["id"],
            "title": (attrs.get("title") or "").strip(),
            "abstract": (attrs.get("description") or "").strip(),
            "authors": _authors(latest, client) if latest.get("embeds", {}).get("bibliographic_contributors") else [],
            "_authors_pending": not bool(latest.get("embeds", {}).get("bibliographic_contributors")),
            "published": first_posted,
            "first_posted_at": first["attributes"]["date_published"],
            "updated": (attrs.get("date_modified") or attrs.get("date_published") or "")[:10],
            "version": _version(latest),
            "withdrawn": bool(attrs.get("date_withdrawn")),
            "date_withdrawn": attrs.get("date_withdrawn"),
            "url": latest.get("links", {}).get("html") or f"https://osf.io/preprints/psyarxiv/{latest['id']}/",
            "doi": doi,
            "published_doi": attrs.get("doi"),
            "tags": attrs.get("tags", []),
            "metrics": {"views": None, "downloads": None, "observed_at": None, "source_url": None, "scope": "not_fetched"},
            "_versions": [{"source_id": row["id"], "published": row["attributes"]["date_published"][:10], "published_at": row["attributes"]["date_published"]} for row in sorted(versions, key=_version)],
            "provenance": {"metadata_url": latest.get("links", {}).get("self"), "first_post_url": first.get("links", {}).get("self"), "provider_query": metadata_url, "first_post_verified": True, "first_post_method": "Public v1 date_published retrieved in the inclusive window"},
        })
    records.sort(key=lambda row: (row["published"], row["id"]), reverse=True)
    return records, older_revisions, withdrawn


def harvest(start: str, end: str, cache_dir: Path, refresh: bool = False) -> dict:
    """Harvest all public PsyArXiv first posts in an inclusive ISO-date window.

    The returned coverage is fail closed. API/pagination/author errors raise;
    cached pages remain available for a subsequent resumable run.
    """
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError("The harvest start must precede its end")
    cutoff = (end_date + timedelta(days=1)).isoformat()
    client = OSFClient(cache_dir, refresh=refresh)
    params = {
        "filter[provider]": "psyarxiv",
        "filter[date_published][gte]": start,
        "filter[date_published][lt]": cutoff,
        "page[size]": 100,
        "sort": "-date_created",
        "fields[users]": "full_name",
        "fields[contributors]": CONTRIBUTOR_FIELDS,
        "fields[preprints]": PREPRINT_FIELDS,
        "version": API_VERSION,
    }
    metadata_url = API + "preprints/?" + urlencode(params)
    pages: list[str] = []
    raw_rows: list[dict] = []
    observations: list[str] = []
    url: str | None = metadata_url
    expected: int | None = None
    while url:
        if url in pages:
            raise RuntimeError("OSF pagination loop")
        pages.append(url)
        response, observed = client.get(url)
        rows = response.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Expected preprint array in OSF provider collection")
        page_total = _collection_total(response)
        if expected is None:
            expected = page_total
        elif expected != page_total:
            raise RuntimeError("PsyArXiv count changed during harvest; rerun with --refresh")
        for row in rows:
            provider = row.get("relationships", {}).get("provider", {}).get("data", {}).get("id")
            if provider != "psyarxiv":
                raise RuntimeError("OSF provider filter failed: " + row.get("id", "unknown"))
            published = row.get("attributes", {}).get("date_published", "")[:10]
            if not start <= published <= end:
                raise RuntimeError("OSF date filter failed: " + row["id"])
        raw_rows.extend(rows)
        observations.append(observed)
        url = response.get("links", {}).get("next")
        if len(pages) == 1 or len(pages) % 10 == 0 or not url:
            print(f"PsyArXiv metadata: {len(raw_rows)}/{expected} version records ({len(pages)} pages)", file=sys.stderr, flush=True)
    if expected is None or expected != len(raw_rows):
        raise RuntimeError(f"Incomplete PsyArXiv metadata harvest: expected {expected}, received {len(raw_rows)}")
    if len({row["id"] for row in raw_rows}) != len(raw_rows):
        raise RuntimeError("Duplicate version rows in PsyArXiv pagination; rerun with --refresh")
    records, older_revisions, withdrawn = _normalize_resources(raw_rows, client, metadata_url)
    coverage = {
        "source": "psyarxiv",
        "status": "complete",
        "complete": True,
        "start": start,
        "end": end,
        "window": {"start": start, "end": end},
        "date_basis": "first public version date_published; inclusive UTC calendar dates",
        "query": metadata_url,
        "reported_version_records": expected,
        "retrieved_version_records": len(raw_rows),
        "metadata_pages": len(pages),
        "unique_first_posts": len(records),
        "old_work_revision_groups_excluded": older_revisions,
        "withdrawn_groups_excluded": withdrawn,
        "excluded_ids": _withdrawn_ids(raw_rows),
        "observed_from": min(observations) if observations else None,
        "observed_to": max(observations) if observations else None,
        "checked_at": _now(),
        "network_requests": client.requests,
        "cache_hits": client.cached,
        "authors_pending": sum(bool(row.get("_authors_pending")) for row in records),
        "limitations": ["Public accepted metadata only; withdrawn/deleted/private works can be absent.", "Snapshot pages are cached; --refresh is needed to obtain a new source snapshot.", "Classification remains a separate title/abstract screening step.", "Full bibliographic authors and usage metrics are enriched after topic screening; raw candidates can have pending author lists."],
    }
    return {"records": records, "coverage": coverage}


fetch_psyarxiv = harvest


def harvest_cached_prefix(start: str, end: str, cache_dir: Path, prefix_file: Path, output: Path) -> dict:
    """Complete a narrowed window from an interrupted, broader sorted API crawl.

    Every missing older-created resource is requested with a disjoint tail
    query. A live narrower-window total must equal the deduplicated union before
    coverage can become complete. The intermediate output is explicitly partial.
    This optimization never substitutes search samples for source coverage.
    """
    prefix = json.loads(Path(prefix_file).read_text(encoding="utf-8"))
    broader = prefix["coverage"]
    if broader["start"] > start or broader["end"] < end:
        raise ValueError("The cached prefix must cover a broader date window")
    page_numbers = set()
    for page in prefix["pages"]:
        params = dict(parse_qsl(urlparse(page["url"]).query))
        if params.get("sort") != "-date_created" or params.get("filter[provider]") != "psyarxiv":
            raise ValueError("The cached prefix lacks a verified provider/creation ordering")
        page_numbers.add(int(params.get("page", "1")))
    if page_numbers != set(range(1, max(page_numbers) + 1)):
        raise ValueError("The broader cached pages do not form a contiguous prefix")
    raw_prefix = prefix["version_resources"]
    if len({row["id"] for row in raw_prefix}) != len(raw_prefix):
        raise ValueError("The cached prefix contains duplicate resources")
    boundary = min(row["attributes"]["date_created"] for row in raw_prefix)
    client = OSFClient(cache_dir, timeout=40)
    params = {"filter[provider]": "psyarxiv", "filter[date_published][gte]": start,
              "filter[date_published][lt]": (date.fromisoformat(end) + timedelta(days=1)).isoformat(),
              "page[size]": 100, "sort": "-date_created", "fields[users]": "full_name",
              "fields[contributors]": CONTRIBUTOR_FIELDS, "fields[preprints]": PREPRINT_FIELDS, "version": API_VERSION}
    query = API + "preprints/?" + urlencode(params)
    first_page, observed = client.get(query)
    expected = _collection_total(first_page)
    raw_by_id = {row["id"]: row for row in raw_prefix if start <= row["attributes"]["date_published"][:10] <= end}
    raw_by_id.update({row["id"]: row for row in first_page["data"]})
    records, older_revisions, withdrawn = _normalize_resources(list(raw_by_id.values()), client, query)
    coverage = {"source": "psyarxiv", "status": "partial", "complete": False,
                "window": {"start": start, "end": end}, "start": start, "end": end,
                "reported_version_records": expected, "retrieved_version_records": len(raw_by_id),
                "unique_first_posts": len(records), "query": query, "checked_at": _now(),
                "date_basis": "first public version date_published; inclusive UTC calendar dates",
                "retrieval_method": "contiguous broader API prefix plus narrower-window older-created tail",
                "prefix_file": str(prefix_file), "prefix_pages": max(page_numbers), "prefix_creation_boundary": boundary,
                "old_work_revision_groups_excluded": older_revisions, "withdrawn_groups_excluded": withdrawn,
                "excluded_ids": _withdrawn_ids(list(raw_by_id.values())),
                "authors_pending": sum(bool(row.get("_authors_pending")) for row in records),
                "limitations": ["Public accepted metadata only; deleted/private versions can be absent.", "Full authors and usage metrics are enriched for shortlisted entries after topic screening.", "API prefix metadata is a preserved earlier snapshot; usage counters have their own observation timestamps."]}
    result = {"records": records, "coverage": coverage}
    _save(output, result)
    print(f"PsyArXiv narrowed cached prefix: {len(raw_by_id)}/{expected} version records; coverage remains partial", file=sys.stderr, flush=True)
    tail_query = API + "preprints/?" + urlencode({**params, "filter[date_created][lte]": boundary})
    try:
        tail, tail_observations = client.collection(tail_query)
        for resource in tail:
            provider = resource.get("relationships", {}).get("provider", {}).get("data", {}).get("id")
            if provider != "psyarxiv" or not start <= resource["attributes"]["date_published"][:10] <= end:
                raise RuntimeError("OSF narrowed-tail filters failed")
            if resource["attributes"]["date_created"] > boundary:
                raise RuntimeError("OSF creation tail filter failed")
            raw_by_id[resource["id"]] = resource
        if expected is None or len(raw_by_id) != expected:
            raise RuntimeError(f"Narrowed-window count mismatch: {len(raw_by_id)} unique resources versus {expected} reported")
        records, older_revisions, withdrawn = _normalize_resources(list(raw_by_id.values()), client, query)
        coverage.update(status="complete", complete=True, retrieved_version_records=len(raw_by_id), unique_first_posts=len(records),
                        tail_query=tail_query, tail_resources=len(tail), observed_from=min([page["fetched_at"] for page in prefix["pages"]] + [observed] + tail_observations),
                        observed_to=max([observed] + tail_observations), checked_at=_now(), old_work_revision_groups_excluded=older_revisions,
                        withdrawn_groups_excluded=withdrawn, authors_pending=sum(bool(row.get("_authors_pending")) for row in records),
                        excluded_ids=_withdrawn_ids(list(raw_by_id.values())),
                        network_requests=client.requests, cache_hits=client.cached)
        result = {"records": records, "coverage": coverage}
        _save(output, result)
        print(f"PsyArXiv narrowed full coverage verified: {len(raw_by_id)}/{expected} unique version resources", file=sys.stderr, flush=True)
        return result
    except (RuntimeError, ValueError, KeyError) as error:
        result["coverage"].update(tail_query=tail_query, failures=[{"error": str(error)}], checked_at=_now())
        _save(output, result)
        raise


def _enrich_record(record: dict, client: OSFClient) -> dict | None:
    views, downloads = 0, 0
    observations: list[str] = []
    urls: list[str] = []
    components: list[dict] = []
    versions: list[dict] = []
    try:
        base = record["source_id"].split("_v")[0]
        versions_url = API + "preprints/" + base + "/versions/?" + urlencode({"version": API_VERSION, "page[size]": 100, "fields[preprints]": PREPRINT_FIELDS})
        public_versions, version_observations = client.collection(versions_url)
        if not public_versions:
            raise RuntimeError("OSF returned no public versions")
        latest = max(public_versions, key=_version)
        # Always display the latest public manuscript metadata. Version lists
        # are unbounded by harvest date, including every older public version.
        attrs = latest["attributes"]
        record["withdrawn"] = bool(attrs.get("date_withdrawn"))
        record["date_withdrawn"] = attrs.get("date_withdrawn")
        if record["withdrawn"]:
            raise RuntimeError("Latest public preprint version has been withdrawn; exclude the work from the library")
        record.update({"source_id": latest["id"], "title": (attrs.get("title") or "").strip(), "abstract": (attrs.get("description") or "").strip(), "authors": _authors(latest, client), "version": _version(latest), "updated": (attrs.get("date_modified") or attrs.get("date_published") or "")[:10], "url": latest.get("links", {}).get("html") or record["url"]})
        doi_url = latest.get("links", {}).get("preprint_doi")
        record["doi"] = doi_url.removeprefix("https://doi.org/").removeprefix("https://dx.doi.org/") if doi_url else record.get("doi")
        record["published_doi"] = attrs.get("doi")
        record.setdefault("provenance", {}).update(metadata_url=latest.get("links", {}).get("self"), version_list_url=versions_url,
                                                   latest_metadata_observed_at=max(version_observations) if version_observations else None)
        record["_authors_pending"] = False
        first = min(public_versions, key=lambda row: _version(row))
        record["first_posted_at"] = first["attributes"]["date_published"]
        versions = [{"source_id": row["id"], "published": (row["attributes"].get("date_published") or "")[:10], "published_at": row["attributes"].get("date_published")} for row in sorted(public_versions, key=_version)]
        record["_versions"] = versions
        for version in versions:
            url = API + "preprints/" + version["source_id"] + "/?" + urlencode({"version": API_VERSION, "metrics[views]": "total", "metrics[downloads]": "total", "fields[preprints]": "title,date_published,version"})
            response, observed = client.get(url)
            metrics = response.get("meta", {}).get("metrics") or response.get("data", {}).get("meta", {}).get("metrics")
            if not metrics or any(type(metrics.get(name)) is not int or metrics[name] < 0 for name in ("views", "downloads")):
                raise RuntimeError("OSF omitted valid usage counters for " + version["source_id"])
            if response.get("data", {}).get("id") != version["source_id"]:
                raise RuntimeError("OSF returned a different preprint version for metrics")
            views += metrics["views"]
            downloads += metrics["downloads"]
            observations.append(observed)
            urls.append(url)
            components.append({"source_id": version["source_id"], "published": version["published"], "published_at": version["published_at"], "views": metrics["views"], "downloads": metrics["downloads"], "observed_at": observed, "source_url": url})
        record["metrics"] = {"views": views, "downloads": downloads, "first_posted_at": record["first_posted_at"], "observed_at": max(observations), "observed_from": min(observations), "source_url": urls[-1], "source_urls": urls, "scope": "all-versions", "coverage_complete": True, "versions": len(versions), "components": components, "version_list_url": versions_url}
        return None
    except (RuntimeError, ValueError, KeyError) as error:
        record["metrics"] = {"views": None, "downloads": None, "observed_at": max(observations) if observations else None, "source_url": urls[-1] if urls else None, "scope": "withdrawn" if record.get("withdrawn") else "partial" if components else "unavailable", "coverage_complete": False, "components": components}
        return {"source_id": record["source_id"], "error": str(error)}


def enrich_metrics(records: list[dict], cache_dir: Path, refresh: bool = False, *, workers: int = 3, on_progress=None) -> dict:
    """Add genuine OSF counts for screened works, summed over their versions.

    Partial/missing counts remain null, with errors reported. Zero is preserved
    only when OSF explicitly returns zero. Mutates matching records in place.
    """
    client = OSFClient(cache_dir, refresh=refresh)
    enriched = 0
    failures: list[dict] = []
    relevant = [row for row in records if row.get("source") == "psyarxiv"]
    with ThreadPoolExecutor(max_workers=max(1, min(3, workers))) as pool:
        futures = [pool.submit(_enrich_record, record, client) for record in relevant]
        for index, future in enumerate(as_completed(futures), 1):
            failure = future.result()
            if failure:
                failures.append(failure)
            else:
                enriched += 1
            progress = {"source": "psyarxiv", "requested": len(relevant), "checked": index, "enriched": enriched,
                        "failures": list(failures), "status": "complete" if index == len(relevant) else "in_progress"}
            if on_progress:
                on_progress(progress)
            if index == 1 or index % 5 == 0 or index == len(relevant):
                print(f"PsyArXiv genuine usage metrics: {index}/{len(relevant)} checked; {enriched} complete", file=sys.stderr, flush=True)
    return {"source": "psyarxiv", "requested": len(relevant), "enriched": enriched, "failures": failures, "network_requests": client.requests, "cache_hits": client.cached, "scope": "all-versions", "official_usage_definition": "https://help.osf.io/article/376-preprints-home-page"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="Inclusive first-post ISO date; defaults to the past 30 days")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--cache-dir", type=Path, default=Path("data/preprints/cache/psyarxiv"))
    parser.add_argument("--output", type=Path, default=Path("data/preprints/psyarxiv.json"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    start = args.start or (date.fromisoformat(args.end) - timedelta(days=30)).isoformat()
    result = harvest(start, args.end, args.cache_dir, args.refresh)
    _save(args.output, result)
    print(json.dumps(result["coverage"], indent=2))


if __name__ == "__main__":
    main()
