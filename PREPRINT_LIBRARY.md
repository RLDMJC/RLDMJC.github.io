# MindRL Preprint Library

The user authorized automatic publication of `/library/` snapshots at `https://rldmjc.github.io/library/` on 13 October 2026 and extended that authorization to future runs with partial retrieval failures on 16 October 2026. After the merged deliverable passes required validation, the daily automation commits the relevant library changes, pushes to `origin main`, and verifies the GitHub Pages deployment, without requesting approval again. Successful articles are published even if other source or article requests failed. Failed requests retain explicit coverage/stale flags and retry state; historical backfill gaps remain disclosed. Errors in validation of the actual deliverable block publication until repaired. The collector never sends speaker invitations.

## Run and review

The harvester uses Python's standard library; it does not require an API key or a paid model. From this repository:

```powershell
python scripts/preprints/update_library.py --start 2026-09-12 --end 2026-10-12 --sources arxiv biorxiv psyarxiv
python -m http.server 8766 --bind 127.0.0.1
```

Open `http://127.0.0.1:8766/library/`. If Windows resolves `python` to the Microsoft Store alias, use the bundled interpreter:

```powershell
& 'C:\Users\Hanbo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/preprints/update_library.py --start 2026-09-12 --end 2026-10-12 --sources arxiv biorxiv psyarxiv
```

The initial completed window was inclusive, **12 September 2026 through 12 October 2026**. The user has now authorized gradual expansion to the past calendar year: the current target is **12 October 2025 through 12 October 2026**. This uses the preprint's first posting date, rather than the date of a revision. Title, abstract, and authors are taken from the latest retrieved version. Usage counts cover all historical versions when the source permits verification. The daily updater refreshes the newest 30-day window and adds one historical month per run, newest first. Archived months and shared HTTP caches survive later updates. Missing months are explicitly marked as incomplete coverage, including when a batch fails. The exact queries, retrieval scope, source failures, and counts are recorded in the generated coverage report. A completed query means its pagination was completed; it does not establish exhaustive recall of every relevant paper.

## Scope and screening

The visible collection keeps only the **rolling past calendar year**, recomputed from each run's America/New_York date. Inclusion uses the **first public posting date**: a later revision does not extend retention. Papers older than the cutoff are omitted from the page, its search/filter counts, and collection exports; there is no separate old-paper section. Local source caches are only provenance and resume evidence, not another visible collection. Historical version counters can still contribute to a retained paper's verified total. With one historical month added per successful daily run, initial year backfill takes roughly twelve runs in total (about ten more after the first two months), subject to source availability and retries.

A paper may enter more than one group; matching any one group is sufficient:

1. **RL & biological learning**: reward learning, reinforcement learning, or related computational mechanisms tied to human or animal behavior, experiments, or neural data.
2. **AI & cognition**: AI combined with cognitive science or psychology, with a task, paradigm, modeling method, measurement method, or research approach contribution.
3. **AI with human implications**: a general AI result with a specific connection to human learning, reasoning, teaching, adaptation, collaboration, or behavioral research. A general performance improvement or a mention of “human feedback” alone is insufficient.

The screening is based on title and abstract metadata and runs automatically. It does not require manually reading every article. Small samples and ambiguous cases can be reviewed to refine the rules. Automated selections are provisional research-discovery entries, not full-text quality judgments. Matched evidence and reasons are retained for auditing. Borderline candidates and exclusions remain available locally for later curation; editorial overrides persist between updates. Reviews by Codex are distinguished from review by a human.

arXiv retrieval uses the exact keyword queries recorded in the coverage manifest across subject categories. bioRxiv retrieval paginates the selected **neuroscience, animal behavior and cognition, bioinformatics, and systems biology** categories for the whole window; it is not an all-category bioRxiv census. PsyArXiv uses its official provider API. Source-specific versions are consolidated; matching DOIs or normalized titles are used to detect cross-listed records, while original source links and provenance are retained.

The database contains the requested title, abstract, author list, and original link, plus small fields needed for filtering, provenance, ranking, and updates. No PDFs are bulk-downloaded.

## Views, downloads, and ranking

Counts are used only when a public source provides **article-level** values. Version-specific counts are summed across every verified version, including earlier versions outside the retrieval window; a source's already-aggregated work-level total is used once. Partial version coverage is identified and is not presented as an all-version total. Missing values are `null`, never zero. Site-wide totals, citations, relevance scores, and visits to this library are not substituted for article views or downloads.

For a measured article:

```text
age_days = max(1, elapsed days from first posting to metric observation)
exposure_fraction = 1 - exp(-age_days / tau_days)
views_age_adjusted = views / exposure_fraction
downloads_age_adjusted = downloads / exposure_fraction
tau_days = 7  # adjustable heuristic, not empirically fitted
```

The user selected exponential-saturation exposure correction so newer and older papers can be compared without assuming indefinite linear accumulation. This assumes cumulative attention follows `A * (1 - exp(-age / tau))`; the corrected score estimates `A` only under that assumption. It is a browsing heuristic, not measured recent growth or scientific quality. A single cumulative snapshot per paper cannot identify the true time constant separately from each paper's popularity. The default **7 days** is configurable with `--exposure-tau-days` and the page control; it is not a fitted estimate. Seven days is a time constant (about 63% of assumed cumulative exposure), not a half-life. Larger values compensate newer papers more strongly. Computation uses `-expm1(-age / tau)` for numerical stability.

Ranking is within each source because definitions and audiences differ across servers. When a source lacks comparable article-level metrics, its default order falls back to latest first and the page says so. Users can also choose publication-date ordering. Observations and exact first-post timestamps are retained when available; otherwise age uses first-post midnight UTC as a date-level approximation. The one-day minimum limits unstable correction immediately after posting. Raw totals remain visible beside corrected scores, and JSON/CSV exports reflect the selected time constant.

- [arXiv public-statistics FAQ](https://info.arxiv.org/help/faq/statfaq.html): arXiv does not publish individual-paper readership/download statistics. Its [API manual](https://info.arxiv.org/help/api/user-manual.html) documents bibliographic metadata without article-level usage fields.
- [bioRxiv API documentation](https://api.biorxiv.org/): article metadata and **aggregate** monthly/yearly usage statistics. Aggregate usage does not support paper ranking. Article-level usage has not been retrieved in this collection; this does not mean bioRxiv has no article-level metrics. Public article metrics pages require separate retrieval and verification of whether their totals already cover all versions.
- [OSF API documentation](https://developer.osf.io/) and [PsyArXiv's count explanation](https://blog.psyarxiv.com/2022/03/17/view-counts-psyarxiv-preprints/): article views/downloads exist; collection uses the fields actually returned by the current endpoint, with observation time and metric source.

If an OSF author account returns HTTP 410, the collector may recover the full ordered author list from Crossref's official registration for that exact version DOI. It requires matching DOI and title, the same bibliographic contributor count, and agreement with every accessible OSF author at the same position. An omitted middle initial is allowed when first and family names agree; conflicting initials or names are rejected. Any mismatch remains a retrieval failure. The registration response is cached and its URL, observation date, and reason are recorded in article provenance; usage counts still come exclusively from verified OSF version counters.

## Daily maintenance and editorial review

The requested schedule is **every day at 09:00 America/New_York**. The active Codex chat automation is **MindRL 每日预印本资料库更新** (ID `mindrl`). It updates metadata and screening output, preserves editorial overrides, and automatically publishes successfully validated changes. It reports meaningful additions, failures, or decisions needed, and never contacts authors. Commits include only relevant library changes, preserve unrelated edits, and never use force-push. Runs with no publishable changes do not create empty commits. Local scheduled work requires this computer and the desktop app to be running; see the [official scheduled-task documentation](https://learn.chatgpt.com/docs/automations).

Each daily run refreshes the full rolling month and then resumes one historical batch. The new month output is staged locally so it does not temporarily replace the year view:

```powershell
python scripts/preprints/update_library.py --window-days 30 --full --refresh --strict --exposure-tau-days 7 --end YYYY-MM-DD --output .local/current-month-library.json
python scripts/preprints/expand_library.py --end YYYY-MM-DD --batch-count 1
python scripts/validate_library.py
```

Use the America/New_York calendar date for `YYYY-MM-DD`. Expansion stages each month under `data/preprints/backfill/`, reuses `data/preprints/cache/`, and validates the merged collection before replacing the page data. `--rebuild-only` rebuilds from saved batches without network access. `data/preprints/backfill-state.json` records progress and remaining per-source gaps. Archived history has its own retrieval dates; it is not represented as freshly checked every day. Once the year is covered, daily maintenance refreshes one oldest-checked historical batch using the pipeline with that batch's start/end, archive data directory, shared cache directory, and `--full --refresh --strict`, then merges it with `expand_library.py --rebuild-only`. This rotates historical revision and usage checks without refreshing the entire year each day. Do not run two expansion processes concurrently.

The strict retrieval command reports incomplete source or PsyArXiv usage retrieval as a failure while retaining an honestly labeled local review artifact. It preserves earlier metadata during source failures; the page identifies incomplete or stale coverage. During backfill, run the validator without `--require-complete`: valid records and an incomplete year are distinct states. Require complete coverage only once every requested interval is verified. The validator checks requested metadata, the first-post window, duplicate IDs, all-version arithmetic, and the ranking denominator.

A nonzero `--strict` retrieval exit records a partial retrieval failure; it is not by itself a publication veto. Continue merging successful data and run the required validator against the rolling-year deliverable, using `--rebuild-only` when needed. New records without verified required title, abstract, full authors, or first-post date remain local; earlier valid records may remain with explicit stale status. A paper with valid required metadata and missing or partial usage can be published with null usage totals and no usage ranking. Never invent fields or weaken validation. Automatically publish the valid portion and rolling-cutoff removals, keep failure evidence and gaps, and retry failed requests next run from saved archives/cache. Partial retrieval failures do not require another user confirmation.

OSF metadata failures skip redundant counter refreshes for the preserved records; their prior observation dates remain unchanged. Enrichment uses a 40-second socket timeout and the existing bounded retries. Once a transient network failure exhausts those retries, that client pauses further uncached network requests for the run; cached evidence can still be replayed and individual non-transient errors such as HTTP 410 do not pause unrelated requests. Enrichment saves its coverage and records after every completed check, including pending status and failures, so interrupted batches remain resumable and cannot be mistaken for complete coverage. A new run creates a fresh client and retries the failed service.

arXiv keeps its 3.1-second request spacing and bounded HTTP retries. If retries exhaust an HTTP 429 rate limit, it preserves completed pages and records the remaining recursive/date intervals as not attempted, without sending more arXiv requests in that run. The next run retries normally. The shared HTTP client retains the final HTTP status in its retrieval exception so rate limits can be distinguished from other errors.

A PsyArXiv withdrawal verified from the latest public version list is recorded separately from retrieval failures. The work is excluded, its counters remain null, and the version-list URL, observation time, and withdrawal date remain in the local audit. This completed exclusion does not leave the entire historical batch pending. Actual version, author, or counter retrieval failures still leave explicit gaps and retry state; prior failure reports remain preserved.

If an unavailable source would make the combined expansion keep retrying only that source, inspect each source's remaining gaps and continue one newest missing historical month for each healthy source with `update_library.py --sources ...`, the dated archive `--data-dir`, and shared `--cache-dir`. Sources with the same dates can share one batch. Retry the failed source's pending batch separately from cache, retain its incomplete coverage, then merge with `expand_library.py --rebuild-only`. Do not repeatedly restart an exhausted outage in the same run or mark the unavailable source complete merely because healthy sources advanced.

When merging archives, a later successful source snapshot replaces older metadata for its overlapping first-post interval. Old snapshots must not resurrect papers that the fresh source removed or withdrew. Failed snapshots retain previous records with explicit gaps, while the rolling-year cutoff still removes expired works. Snapshot ordering uses checked timestamps, not archive directory names.

Replaying a cached DOI history must preserve an older first post discovered through a newer revision when that first-post month has not yet been covered. Such carried records are suppressed when a newer authoritative check covered their first-post date or explicitly excluded their ID; cache age alone must not erase this provenance.

The browser shortlist and author tracking are stored in this browser on this origin. Use their export control to preserve or share the list. They do not create invitations or infer that an author has agreed to speak; name-based tracking may group namesakes and needs editorial review.

This repository contains a generated Hugo export, without the original theme/content source. `scripts/integrate_library.py` adds the Library navigation, home-page entry, and search-index entry and can be reapplied after a Hugo rebuild. It preserves adjacent `*.pre-library.bak` backups on the first edit. The new library files must also be retained during regeneration.

Publication of the initial snapshot was authorized on 12 October 2026; automatic publication of subsequent successful updates was authorized on 13 October 2026.

On 14 October 2026, the user explicitly authorized publication of the validated 884-paper snapshot despite the disclosed PsyArXiv author-account HTTP 410 failure. The affected work is excluded from the public collection, and the source failure and incomplete historical coverage remain recorded. This snapshot-specific approval does not remove the automatic publication checks for future runs.

On 15 October 2026, the deleted-account author failure was resolved through the exact-version Crossref registration checks above. The article now has a verified ordered author list and all-version OSF counters and can enter the collection; the earlier failure evidence remains local.

On 16 October 2026, the user explicitly approved publication of the validated 1,187-paper snapshot despite incomplete historical PsyArXiv version/usage checks. The next daily run must retry the batch from 12 May through 11 June 2026 using its saved archive and shared cache. Metadata pagination completed, but the first of 37 usage checks failed after retries and remaining checks were aborted during endpoint timeouts. Missing counters remain unavailable, source coverage remains incomplete, and failure evidence is saved in `.local/psyarxiv-usage-failure-2026-10-16.json`. This snapshot-specific approval preserves the normal automatic-publication checks for future runs.

Later on 16 October 2026, the user extended this authorization to all future partial retrieval failures: publish other valid articles automatically, retain failures for the next run, and do not wait for repeated approval. This standing policy supersedes the earlier snapshot-specific restriction; required validation of the published deliverable remains mandatory.
