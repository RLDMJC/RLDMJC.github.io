"""Paginate public arXiv Atom search, splitting large date intervals recursively."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import urlencode

from common import HTTPClient, FetchError, utc_now

ATOM = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
API = "https://export.arxiv.org/api/query"


def terms(values):
    return "(" + " OR ".join(f'(ti:"{x}" OR ab:"{x}")' for x in values) + ")"


AI = terms(["artificial intelligence", "machine learning", "deep learning", "language model", "LLM", "foundation model", "neural network", "transformer"])
BIO = terms(["human", "animal", "cognitive", "psychology", "psychological", "neuroscience", "brain", "behavioral", "behavioural", "rodent", "mice", "monkey", "primate"])
COGNITION = terms(["cognitive", "cognition", "psychology", "psychological", "theory of mind", "human reasoning", "human behavior", "human behaviour", "human learning", "decision making", "decision-making"])
IMPLICATION = terms(["human learning", "human feedback", "human-AI", "human AI", "education", "tutoring", "collaboration", "continual learning", "lifelong learning", "meta-learning", "in-context learning"])
# The API returns HTTP 500 for very long Boolean queries. Use a compact, broad
# retrieval union; the title/abstract screener below supplies the specificity.
SEARCH = '(all:reinforcement OR all:cognitive OR all:psychology OR all:human OR all:animal OR all:brain OR all:mice OR all:rodent OR all:monkey)'


def parse_atom(raw, meta):
    try:
        feed = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FetchError(f"Invalid arXiv Atom: {exc}") from exc
    total_text = feed.findtext("o:totalResults", namespaces=ATOM)
    if total_text is None:
        raise FetchError("arXiv feed lacks totalResults (possibly an API error)")
    records = []
    for entry in feed.findall("a:entry", ATOM):
        identifier = entry.findtext("a:id", "", ATOM)
        if "api/errors" in identifier:
            raise FetchError(entry.findtext("a:summary", "arXiv API error", ATOM))
        source_id = re.sub(r"v\d+$", "", identifier.split("/abs/")[-1])
        version_match = re.search(r"v(\d+)$", identifier)
        comment = entry.findtext("x:comment", "", ATOM)
        records.append({
            "id": f"arxiv:{source_id}", "source": "arxiv", "source_id": source_id,
            "title": " ".join(entry.findtext("a:title", "", ATOM).split()),
            "abstract": " ".join(entry.findtext("a:summary", "", ATOM).split()),
            "authors": [a.findtext("a:name", "", ATOM) for a in entry.findall("a:author", ATOM)],
            "published": entry.findtext("a:published", "", ATOM)[:10],
            "updated": entry.findtext("a:updated", "", ATOM),
            "doi": entry.findtext("x:doi", None, ATOM),
            "url": f"https://arxiv.org/abs/{source_id}",
            "version": int(version_match.group(1)) if version_match else 1,
            "withdrawn": bool(re.search(r"\b(?:paper|article|submission) (?:has been|is) withdrawn\b|\bwithdrawn by (?:the )?authors\b", comment, re.I)),
            "source_categories": [x.get("term") for x in entry.findall("a:category", ATOM)],
            "provenance": {"metadata_url": meta["url"], "fetched_at": meta["fetched_at"], "sha256": meta["sha256"], "first_post_verified": True},
        })
    return records, int(total_text)


def harvest(start, end, cache_dir, refresh=False):
    client = HTTPClient(cache_dir, refresh=refresh, delay=3.1)
    records, intervals, failures = [], [], []

    def page(lo, hi, offset=0, limit=500):
        query = SEARCH + f" AND submittedDate:[{str(lo).replace('-', '')}0000 TO {str(hi).replace('-', '')}2359]"
        url = API + "?" + urlencode({"search_query": query, "start": offset, "max_results": limit,
                                     "sortBy": "submittedDate", "sortOrder": "ascending"})
        raw, meta = client.get(url)
        return parse_atom(raw, meta)

    def interval(lo, hi):
        try:
            _, total = page(lo, hi, limit=1)
            if total > 1000 and lo < hi:
                middle = lo + (hi - lo) // 2
                interval(lo, middle)
                interval(middle + timedelta(days=1), hi)
                return
            fetched = 0
            while fetched < total:
                batch, current_total = page(lo, hi, fetched)
                if not batch:
                    raise FetchError(f"Empty arXiv page before total ({fetched}/{total})")
                total = max(total, current_total)
                records.extend(batch)
                fetched += len(batch)
                print(f"arxiv {lo}..{hi}: {fetched}/{total}", flush=True)
            intervals.append({"start": str(lo), "end": str(hi), "total": total, "fetched": fetched, "status": "complete"})
        except (FetchError, ValueError) as exc:
            failures.append({"start": str(lo), "end": str(hi), "error": str(exc)})
            print(f"arxiv FAILED {lo}..{hi}: {exc}", flush=True)

    # Inclusive monthly intervals keep changing API result sets small.
    cursor, final = date.fromisoformat(start), date.fromisoformat(end)
    while cursor <= final:
        following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        upper = min(final, following - timedelta(days=1))
        interval(cursor, upper)
        cursor = upper + timedelta(days=1)
    return {"records": records, "coverage": {
        "source": "arxiv", "status": "complete" if not failures else ("partial" if records else "failed"),
        "fetched": len(records), "window": {"start": start, "end": end}, "query": SEARCH,
        "scope": "Broad all-metadata keyword retrieval across all arXiv categories, followed by conservative title/abstract screening; not an exhaustive census of all relevant papers.",
        "intervals": intervals, "failures": failures, "requests": client.requests, "cache_hits": client.cache_hits,
        "checked_at": utc_now(), "metrics_status": "Per-paper views/downloads not supplied by the public arXiv metadata API.",
    }}
