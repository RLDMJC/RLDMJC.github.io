'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const STORAGE_KEY = 'mindrl-library-reading-list-v1';
  const PAGE_SIZE = 40;
  const SOURCE_ORDER = ['arxiv', 'psyarxiv', 'biorxiv'];
  const SOURCE_NAMES = { arxiv: 'arXiv', psyarxiv: 'PsyArXiv', biorxiv: 'bioRxiv' };
  const DEFAULT_CATEGORIES = [
    { id: 'rl-learning', label: 'RL & human / animal learning', badge: 'RL & learning' },
    { id: 'ai-cognition', label: 'AI & cognitive science', badge: 'AI & cognition' },
    { id: 'ai-implications', label: 'AI with implications for learning', badge: 'AI & learning' }
  ];
  const state = { data: null, papers: [], categories: DEFAULT_CATEGORIES, sources: new Set(), category: 'all', query: '', sort: 'views', exposureTauDays: 7, from: '', to: '', savedOnly: false, trackedOnly: false, limit: PAGE_SIZE, filtered: [], saved: new Set(), authors: new Set(), storageAvailable: true };
  const number = new Intl.NumberFormat('en-US');
  const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });

  function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }

  function validDate(value) {
    if (!value) return null;
    const result = new Date(value);
    return Number.isFinite(result.getTime()) ? result : null;
  }

  function formatDate(value) {
    const date = validDate(value);
    return date ? day.format(date) : 'Date unavailable';
  }

  function sourceKey(value) { return String(value || 'unknown').toLowerCase().replace(/[^a-z]/g, ''); }
  function sourceName(value) { return SOURCE_NAMES[sourceKey(value)] || String(value || 'Unknown source'); }
  function normalizeAuthor(value) { return String(value || '').trim().replace(/\s+/g, ' '); }
  function announcement(value) { $('library-announcement').textContent = value; }
  function completeVersionScope(paper) {
    const metrics = paper.metrics || {};
    const scope = String(metrics.scope || metrics.version_scope || '').toLowerCase().replace(/[-\s]/g, '_');
    const complete = metrics.coverage_complete === true || metrics.aggregation?.complete === true;
    const incomplete = metrics.coverage_complete === false || metrics.aggregation?.complete === false;
    return !incomplete && complete && ['all_versions', 'all_public_versions', 'sum_of_public_versions'].includes(scope);
  }
  function rankingSource(paper) {
    return sourceKey(paper.metrics?.preprint_source || paper.source);
  }
  function metricAgeDays(paper) {
    const observed = validDate(paper.metrics?.observed_at);
    const posted = validDate(paper.metrics?.first_posted_at || paper.metrics?.first_post_date || paper.first_posted_at || paper.published);
    return observed && posted && observed >= posted ? Math.max(1, (observed - posted) / 86400000) : null;
  }
  function metricScore(paper, kind) {
    const count = paper.metrics && paper.metrics[kind];
    const ageDays = metricAgeDays(paper);
    const tau = state.exposureTauDays;
    if (!completeVersionScope(paper) || typeof count !== 'number' || !Number.isFinite(count) || count < 0 || ageDays === null || !Number.isFinite(tau) || tau <= 0) return null;
    const score = count / -Math.expm1(-ageDays / tau);
    return Number.isFinite(score) ? score : null;
  }

  function readStorage() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      state.saved = new Set(Array.isArray(value.saved) ? value.saved.filter(id => typeof id === 'string') : []);
      state.authors = new Set(Array.isArray(value.authors) ? value.authors.map(normalizeAuthor).filter(Boolean) : []);
    } catch (_) {
      state.storageAvailable = false;
      $('storage-note').textContent = 'Browser storage is unavailable. Export your list before leaving.';
    }
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, saved: [...state.saved], authors: [...state.authors] }));
    } catch (_) {
      state.storageAvailable = false;
      $('storage-note').textContent = 'Browser storage is unavailable. Export your list before leaving.';
    }
    renderPersonalCounts();
  }

  function renderPersonalCounts() {
    $('saved-count').textContent = number.format(state.saved.size);
    $('tracked-count').textContent = number.format(state.authors.size);
    const list = $('tracked-authors-list');
    list.replaceChildren();
    if (!state.authors.size) list.append(element('p', 'muted', 'Track an author from a paper.'));
    [...state.authors].sort((a, b) => a.localeCompare(b)).forEach(author => {
      const row = element('div', 'tracked-author-item');
      row.append(element('span', '', author));
      const remove = element('button', 'remove-author', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `Stop tracking ${author}`);
      remove.addEventListener('click', () => { state.authors.delete(author); persist(); render(); announcement(`Stopped tracking ${author}.`); });
      row.append(remove);
      list.append(row);
    });
  }

  function initializeFilters() {
    const categories = $('category-filters');
    state.categories.forEach(category => {
      const button = element('button', 'category-choice');
      button.type = 'button';
      button.dataset.category = category.id;
      button.setAttribute('aria-pressed', 'false');
      button.append(element('span', '', category.label), element('span', 'filter-count', number.format(state.papers.filter(p => p.categories.includes(category.id)).length)));
      categories.append(button);
    });
    $('all-category-count').textContent = number.format(state.papers.length);
    categories.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
      state.category = button.dataset.category;
      categories.querySelectorAll('button').forEach(item => {
        const selected = item === button;
        item.classList.toggle('is-selected', selected);
        item.setAttribute('aria-pressed', String(selected));
      });
      resetPagination();
    }));
    const sourceValues = new Set(SOURCE_ORDER);
    state.papers.forEach(p => sourceValues.add(p.source));
    (state.data.coverage || []).forEach(item => sourceValues.add(sourceKey(item.source)));
    sourceValues.forEach(source => {
      state.sources.add(source);
      const label = element('label', 'checkbox-label');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.checked = true;
      checkbox.value = source;
      checkbox.addEventListener('change', () => { checkbox.checked ? state.sources.add(source) : state.sources.delete(source); resetPagination(); });
      label.append(checkbox, element('span', '', sourceName(source)), element('span', 'filter-count', number.format(state.papers.filter(p => p.source === source).length)));
      $('source-filters').append(label);
    });
    const window = state.data.window || {};
    if (window.start) $('date-from').min = String(window.start).slice(0, 10);
    if (window.end) $('date-to').max = String(window.end).slice(0, 10);
  }

  function renderSnapshot() {
    $('snapshot-total').textContent = number.format(state.papers.length);
    const window = state.data.window || {};
    $('snapshot-window').textContent = window.start && window.end ? `${formatDate(window.start)} – ${formatDate(window.end)}` : 'Collection window unavailable';
    $('snapshot-updated').textContent = state.data.generated_at ? `Snapshot: ${formatDate(state.data.generated_at)}` : 'Snapshot date unavailable';
    if (state.data.backfill?.status === 'in_progress') {
      $('snapshot-window').textContent = `Target: ${$('snapshot-window').textContent}`;
      $('snapshot-updated').textContent += ' · Year backfill in progress';
    }
    const coverage = $('coverage-details');
    const summary = $('coverage-summary');
    summary.replaceChildren();
    (state.data.coverage || []).forEach(item => {
      const metadataStatus = String(item.status || 'Unknown').replace(/[_-]/g, ' ');
      const scope = typeof item.scope === 'string' ? item.scope : Array.isArray(item.scope) ? item.scope.join(', ') : item.scope?.description || item.scope?.note || '';
      const failures = Array.isArray(item.failures) ? item.failures.length : Number.isFinite(item.failure_count) ? item.failure_count : 0;
      const reportedMetricFailures = Array.isArray(item.metrics_enrichment?.failures) ? item.metrics_enrichment.failures.length : Array.isArray(item.metrics?.failures) ? item.metrics.failures.length : Array.isArray(item.metric_failures) ? item.metric_failures.length : Number.isFinite(item.metric_failure_count) ? item.metric_failure_count : 0;
      const metricFailures = Math.max(reportedMetricFailures, item.metrics_error ? 1 : 0);
      const status = metricFailures ? `${metadataStatus} metadata · usage incomplete` : metadataStatus;
      const incomplete = item.complete === false || !['complete', 'ok', 'success'].includes(String(item.status || '').toLowerCase()) || failures > 0 || metricFailures > 0;
      const visible = element('div', `coverage-summary-item${incomplete ? ' coverage-incomplete' : ''}`);
      visible.append(element('span', 'coverage-indicator'), element('strong', '', sourceName(item.source)), element('span', '', status));
      if (failures || metricFailures) visible.append(element('span', '', `${number.format(failures + metricFailures)} reported failures`));
      summary.append(visible);
      const box = element('div', 'coverage-item');
      box.append(element('strong', '', sourceName(item.source)), element('span', 'coverage-state', status));
      const parts = [];
      if (Number.isFinite(item.fetched)) parts.push(`${number.format(item.fetched)} records fetched`);
      if (Number.isFinite(item.retained)) parts.push(`${number.format(item.retained)} retained`);
      box.append(element('p', '', parts.join(' · ') || 'Coverage information unavailable.'));
      const note = item.note || item.reason || item.error || item.notes;
      if (note) box.append(element('p', '', Array.isArray(note) ? note.join(' ') : String(note)));
      if (scope) box.append(element('p', '', `Scope: ${scope}`));
      if (item.metrics_status) box.append(element('p', '', `Usage counters: ${String(item.metrics_status)}`));
      if (Number.isFinite(item.metrics_enrichment?.requested)) box.append(element('p', '', `Verified all-version counters: ${number.format(item.metrics_enrichment.enriched || 0)} of ${number.format(item.metrics_enrichment.requested)} selected works.`));
      if (item.metrics_error) box.append(element('p', '', 'Usage collection failed; unavailable or partial counters are excluded from usage ranking.'));
      if (failures && item.previous_checked_at) box.append(element('p', '', `Previous metadata preserved from ${formatDate(item.previous_checked_at)}.`));
      if (failures || metricFailures) box.append(element('p', '', `${number.format(failures)} metadata failures; ${number.format(metricFailures)} usage-counter failures.`));
      coverage.append(box);
    });
    if (!(state.data.coverage || []).length) summary.append(element('p', 'muted', 'Source coverage was not recorded in this snapshot.'));
    const screening = state.data.screening || {};
    $('screening-method').textContent = typeof screening === 'string' ? screening : screening.description || screening.method || 'The collection reflects the screening decisions stored with each record. See the JSON export for provenance and inclusion rationale.';
  }

  function relevance(paper) {
    const terms = state.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    if (!terms.length) return 0;
    const fields = [[paper.title, 4], [paper.authors.join(' '), 3], [paper.abstract, 1], [paper.tags.join(' '), 1]];
    return terms.reduce((score, term) => score + fields.reduce((value, [text, weight]) => value + (String(text).toLowerCase().includes(term) ? weight : 0), 0), 0);
  }

  function newest(a, b) {
    return (validDate(b.published)?.getTime() || 0) - (validDate(a.published)?.getTime() || 0) || a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
  }

  function comparePapers(a, b) {
    if (state.sort === 'views' || state.sort === 'downloads') {
      const aScore = metricScore(a, state.sort), bScore = metricScore(b, state.sort);
      if (aScore !== null && bScore === null) return -1;
      if (aScore === null && bScore !== null) return 1;
      if (aScore !== null && bScore !== null && aScore !== bScore) return bScore - aScore;
    }
    if (state.sort === 'relevance') { const difference = relevance(b) - relevance(a); if (difference) return difference; }
    return newest(a, b);
  }

  function getFiltered() {
    const terms = state.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    return state.papers.filter(paper => {
      if (!state.sources.has(paper.source)) return false;
      if (state.category !== 'all' && !paper.categories.includes(state.category)) return false;
      if (state.savedOnly && !state.saved.has(paper.id)) return false;
      if (state.trackedOnly && !paper.authors.some(author => state.authors.has(author))) return false;
      const date = String(paper.published || '').slice(0, 10);
      if (state.from && (!date || date < state.from)) return false;
      if (state.to && (!date || date > state.to)) return false;
      if (terms.length) {
        const haystack = `${paper.title} ${paper.abstract} ${paper.authors.join(' ')} ${paper.tags.join(' ')}`.toLowerCase();
        if (!terms.every(term => haystack.includes(term))) return false;
      }
      return true;
    });
  }

  function icon(kind) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.6'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', kind === 'bookmark' ? 'M6 3h12v18l-6-4-6 4V3Z' : 'M12 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 9v-2a7 7 0 0 1 14 0v2M19 3v5m-2.5-2.5h5');
    svg.append(path);
    return svg;
  }

  function createCard(paper) {
    const card = element('article', 'paper-card');
    const top = element('div', 'paper-topline');
    top.append(element('span', 'source-badge', sourceName(paper.source)), element('time', '', formatDate(paper.published)));
    if (validDate(paper.published)) top.querySelector('time').dateTime = paper.published;
    paper.categories.forEach(id => { const category = state.categories.find(c => c.id === id); if (category) top.append(element('span', 'category-badge', category.badge || DEFAULT_CATEGORIES.find(c => c.id === id)?.badge || category.label)); });
    card.append(top);
    const title = element('h3');
    const link = element('a', '', paper.title);
    try {
      const url = new URL(paper.url);
      if (url.protocol === 'https:' || url.protocol === 'http:') { link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    } catch (_) { /* A malformed source URL remains plain text. */ }
    if (link.href) { const cue = element('span', 'external-cue', '↗'); cue.setAttribute('aria-hidden', 'true'); link.append(cue); }
    title.append(link);
    card.append(title, element('p', 'paper-authors', paper.authors.length ? paper.authors.join(', ') : 'Authors unavailable'));
    const abstract = element('details', 'abstract-detail');
    abstract.append(element('summary', '', 'Read abstract'), element('p', 'abstract-text', paper.abstract || 'The source did not provide an abstract.'));
    card.append(abstract);
    const bottom = element('div', 'paper-bottomline');
    const metrics = element('div', 'paper-metrics');
    ['views', 'downloads'].forEach(kind => {
      const score = metricScore(paper, kind);
      const count = paper.metrics?.[kind];
      const hasCount = typeof count === 'number' && Number.isFinite(count) && count >= 0;
      const label = score === null ? hasCount ? `${number.format(count)} ${kind} · correction unavailable` : `${kind[0].toUpperCase() + kind.slice(1)} unavailable` : `${number.format(count)} ${kind} · adjusted ${new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(score)}`;
      const badge = element('span', score === null ? '' : 'metric-available', label);
      if (score !== null) badge.title = `All historical public versions. Observed ${formatDate(paper.metrics.observed_at)}. Score = count / (1 − exp(−age / ${state.exposureTauDays} days)), using the first posting on ${sourceName(rankingSource(paper))}, with a one-day minimum. This assumed curve is not fitted to observed traffic and does not measure scientific quality or recent growth.`;
      metrics.append(badge);
    });
    if (completeVersionScope(paper)) {
      const versions = Array.isArray(paper.metrics.components) ? paper.metrics.components.length : Number.isFinite(paper.metrics.versions) ? paper.metrics.versions : null;
      metrics.append(element('span', 'metric-scope', `${sourceName(rankingSource(paper))} · ${versions ? `all ${versions} public ${versions === 1 ? 'version' : 'versions'}` : 'all public versions'}`));
    }
    const actions = element('div', 'paper-actions');
    const save = element('button', 'paper-action');
    save.type = 'button';
    save.append(icon('bookmark'), element('span', '', state.saved.has(paper.id) ? 'Saved' : 'Save'));
    save.setAttribute('aria-pressed', String(state.saved.has(paper.id)));
    save.setAttribute('aria-label', `${state.saved.has(paper.id) ? 'Unsave' : 'Save'} ${paper.title}`);
    save.addEventListener('click', () => {
      const saved = !state.saved.has(paper.id);
      saved ? state.saved.add(paper.id) : state.saved.delete(paper.id);
      persist();
      save.setAttribute('aria-pressed', String(saved));
      save.setAttribute('aria-label', `${saved ? 'Unsave' : 'Save'} ${paper.title}`);
      save.querySelector('span').textContent = saved ? 'Saved' : 'Save';
      announcement(`${saved ? 'Saved' : 'Removed'} ${paper.title}.`);
      if (state.savedOnly) render();
    });
    const track = element('button', 'paper-action');
    track.type = 'button'; track.append(icon('author'), element('span', '', 'Track authors'));
    track.setAttribute('aria-expanded', 'false');
    track.setAttribute('aria-label', `Track authors of ${paper.title}`);
    const tracking = element('div', 'author-tracking');
    tracking.hidden = true;
    tracking.append(element('h4', '', 'Keep an eye on these authors'));
    const choices = element('div', 'author-choices');
    paper.authors.forEach(author => {
      const label = element('label', 'checkbox-label');
      const checkbox = element('input');
      checkbox.type = 'checkbox'; checkbox.checked = state.authors.has(author);
      checkbox.addEventListener('change', () => {
        checkbox.checked ? state.authors.add(author) : state.authors.delete(author);
        persist();
        document.querySelectorAll('.author-choices input').forEach(item => { if (item.dataset.author === author) item.checked = checkbox.checked; });
        announcement(`${checkbox.checked ? 'Tracking' : 'Stopped tracking'} ${author}.`);
        if (state.trackedOnly) render();
      });
      checkbox.dataset.author = author;
      label.append(checkbox, element('span', '', author)); choices.append(label);
    });
    tracking.append(choices, element('p', '', 'Stored in this browser. Match by author name; different researchers can share a name.'));
    track.addEventListener('click', () => { tracking.hidden = !tracking.hidden; track.setAttribute('aria-expanded', String(!tracking.hidden)); });
    if (!paper.authors.length) track.disabled = true;
    actions.append(save, track); bottom.append(metrics, actions); card.append(bottom, tracking);
    return card;
  }

  function render() {
    if (!state.data) return;
    const results = $('paper-results');
    const filtered = getFiltered();
    const metricSort = state.sort === 'views' || state.sort === 'downloads';
    if (metricSort) {
      const metricSources = new Set(filtered.filter(p => metricScore(p, state.sort) !== null).map(rankingSource));
      filtered.sort((a, b) => {
        const aSource = rankingSource(a), bSource = rankingSource(b);
        const metricAvailability = Number(metricSources.has(bSource)) - Number(metricSources.has(aSource));
        const aIndex = SOURCE_ORDER.indexOf(aSource), bIndex = SOURCE_ORDER.indexOf(bSource);
        const sourceOrder = (aIndex < 0 ? 99 : aIndex) - (bIndex < 0 ? 99 : bIndex) || aSource.localeCompare(bSource);
        return metricAvailability || sourceOrder || comparePapers(a, b);
      });
    } else filtered.sort(comparePapers);
    state.filtered = filtered;
    $('result-count').textContent = `${number.format(filtered.length)} ${filtered.length === 1 ? 'paper' : 'papers'}${state.papers.length !== filtered.length ? ` of ${number.format(state.papers.length)}` : ' in the collection'}`;
    let note;
    if (metricSort) {
      const available = filtered.filter(p => metricScore(p, state.sort) !== null).length;
      note = available ? `${number.format(available)} papers have verified all-version ${state.sort}. Age correction uses an assumed exponential saturation curve (τ = ${state.exposureTauDays} days), not fitted traffic data. Scores are ranked within each platform; unavailable totals follow newest first.` : `Verified all-version ${state.sort} are unavailable for these results. Papers are grouped by source and ordered newest first; missing counts are never treated as zero.`;
    } else if (state.sort === 'relevance') note = state.query.trim() ? 'Search relevance uses matches in the title, abstract, author names, and tags. Ties are ordered newest first.' : 'Enter a search to rank by relevance. Until then, papers are ordered newest first.';
    else note = 'Ordered by first posting date, across all selected sources. New versions do not reset a paper’s age.';
    $('ranking-note').textContent = note;
    if ($('ranking-tau')) $('ranking-tau').disabled = !metricSort;
    results.replaceChildren();
    results.setAttribute('aria-busy', 'false');
    if (!filtered.length) {
      const empty = element('div', 'empty-state');
      empty.append(element('h2', '', state.papers.length ? 'No papers match this view' : 'The collection is waiting for its first papers'), element('p', '', state.papers.length ? 'Try a broader search, a different reading area, or reset your filters.' : 'The source coverage details above explain the current snapshot.'));
      if (state.papers.length) { const button = element('button', 'outline-button', 'Reset filters'); button.type = 'button'; button.addEventListener('click', resetFilters); empty.append(button); }
      results.append(empty);
    } else {
      const shown = filtered.slice(0, state.limit);
      if (metricSort) {
        let currentSource = null, section;
        shown.forEach(paper => {
          const source = rankingSource(paper);
          if (currentSource !== source) {
            currentSource = source;
            section = element('section', 'source-section');
            section.setAttribute('aria-label', `${sourceName(source)} usage ranking`);
            const heading = element('div', 'source-heading');
            heading.append(element('h2', '', sourceName(source)), element('span', '', `${number.format(filtered.filter(p => rankingSource(p) === source).length)} papers`));
            section.append(heading); results.append(section);
          }
          section.append(createCard(paper));
        });
      } else shown.forEach(paper => results.append(createCard(paper)));
    }
    $('pagination').hidden = filtered.length <= state.limit;
    $('page-progress').textContent = `Showing ${number.format(Math.min(state.limit, filtered.length))} of ${number.format(filtered.length)} papers`;
  }

  function resetPagination() { state.limit = PAGE_SIZE; render(); }
  function resetFilters() {
    state.query = ''; state.category = 'all'; state.from = ''; state.to = ''; state.savedOnly = false; state.trackedOnly = false;
    $('paper-search').value = ''; $('date-from').value = ''; $('date-to').value = ''; $('saved-only').checked = false; $('tracked-only').checked = false;
    $('source-filters').querySelectorAll('input').forEach(input => { input.checked = true; state.sources.add(input.value); });
    $('category-filters').querySelectorAll('button').forEach(button => { const selected = button.dataset.category === 'all'; button.classList.toggle('is-selected', selected); button.setAttribute('aria-pressed', String(selected)); });
    resetPagination();
  }

  function download(filename, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const anchor = element('a'); anchor.href = url; anchor.download = filename;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function csvValue(value) {
    let text = Array.isArray(value) ? value.join('; ') : String(value ?? '');
    // Prevent titles and other third-party strings from becoming formulas in spreadsheet programs.
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  }

  function attachControls() {
    $('theme-toggle').addEventListener('click', () => {
      document.body.classList.toggle('dark');
      try { localStorage.setItem('pref-theme', document.body.classList.contains('dark') ? 'dark' : 'light'); } catch (_) { /* Theme still works without persistent storage. */ }
    });
    let searchTimer;
    $('paper-search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value; resetPagination(); }, 160); });
    $('sort-order').addEventListener('change', event => { state.sort = event.target.value; resetPagination(); });
    const updateExposureTimeConstant = event => {
      const tau = Number(event.target.value);
      if (!Number.isFinite(tau) || tau <= 0 || !event.target.checkValidity()) {
        if (event.type === 'change') {
          event.target.value = state.exposureTauDays;
          announcement('Choose a positive exposure time constant.');
        }
        return;
      }
      if (tau === state.exposureTauDays) return;
      state.exposureTauDays = tau;
      resetPagination();
      announcement(`Age correction updated with a ${tau}-day time constant.`);
    };
    $('ranking-tau')?.addEventListener('input', updateExposureTimeConstant);
    $('ranking-tau')?.addEventListener('change', updateExposureTimeConstant);
    $('date-from').addEventListener('change', event => { state.from = event.target.value; resetPagination(); });
    $('date-to').addEventListener('change', event => { state.to = event.target.value; resetPagination(); });
    $('saved-only').addEventListener('change', event => { state.savedOnly = event.target.checked; resetPagination(); });
    $('tracked-only').addEventListener('change', event => { state.trackedOnly = event.target.checked; resetPagination(); });
    $('reset-filters').addEventListener('click', resetFilters);
    $('load-more').addEventListener('click', () => { state.limit += PAGE_SIZE; render(); announcement(`Showing ${Math.min(state.limit, state.filtered.length)} papers.`); });
    document.addEventListener('keydown', event => {
      const tag = document.activeElement?.tagName;
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && !document.activeElement?.isContentEditable) { event.preventDefault(); $('paper-search').focus(); }
    });
    $('export-reading-list').addEventListener('click', () => download('mindrl-reading-list.json', JSON.stringify({ version: 1, exported_at: new Date().toISOString(), saved: [...state.saved], authors: [...state.authors], papers: state.papers.filter(p => state.saved.has(p.id)).map(p => ({ id: p.id, title: p.title, authors: p.authors, url: p.url })) }, null, 2), 'application/json'));
    $('import-reading-list').addEventListener('click', () => $('reading-list-file').click());
    $('reading-list-file').addEventListener('change', async event => {
      const file = event.target.files[0];
      if (!file) return;
      try {
        if (file.size > 2000000) throw new Error('The reading list is too large (maximum 2 MB).');
        const imported = JSON.parse(await file.text());
        if (!Array.isArray(imported.saved) || !Array.isArray(imported.authors)) throw new Error('This file is not a MindRL reading list.');
        imported.saved.filter(id => typeof id === 'string').forEach(id => state.saved.add(id));
        imported.authors.filter(name => typeof name === 'string').map(normalizeAuthor).filter(Boolean).forEach(name => state.authors.add(name));
        persist(); resetPagination(); announcement('Reading list imported and merged with your current list.');
        $('storage-note').textContent = state.storageAvailable ? 'List imported. Saved only in this browser.' : 'List imported. Export before leaving; browser storage is unavailable.';
      } catch (error) { $('storage-note').textContent = error.message; announcement(error.message); }
      event.target.value = '';
    });
    $('export-json').addEventListener('click', () => {
      if (!state.data) return;
      const papers = state.filtered.map(p => {
        const ageDays = metricAgeDays(p);
        return { ...p, metrics: { ...p.metrics, age_days: ageDays, exposure_fraction: ageDays === null ? null : -Math.expm1(-ageDays / state.exposureTauDays), views_age_adjusted: metricScore(p, 'views'), downloads_age_adjusted: metricScore(p, 'downloads'), exposure_tau_days: state.exposureTauDays } };
      });
      download('mindrl-preprint-library.json', JSON.stringify({ ...state.data, ranking: { ...state.data.ranking, exposure_tau_days: state.exposureTauDays }, papers }, null, 2), 'application/json');
    });
    $('export-csv').addEventListener('click', () => {
      if (!state.data) return;
      const fields = ['title', 'abstract', 'authors', 'source', 'published', 'url', 'categories', 'views', 'downloads', 'metric_observed_at', 'views_age_adjusted', 'downloads_age_adjusted', 'exposure_tau_days'];
      const rows = state.filtered.map(p => [p.title, p.abstract, p.authors, sourceName(p.source), p.published, p.url, p.categories, p.metrics?.views, p.metrics?.downloads, p.metrics?.observed_at, metricScore(p, 'views'), metricScore(p, 'downloads'), state.exposureTauDays]);
      download('mindrl-preprint-library.csv', '\uFEFF' + [fields, ...rows].map(row => row.map(csvValue).join(',')).join('\r\n'), 'text/csv;charset=utf-8');
    });
  }

  async function load() {
    readStorage(); renderPersonalCounts(); attachControls();
    try {
      const response = await fetch('./data.json', { cache: 'no-cache' });
      if (!response.ok) throw new Error(`The metadata file returned HTTP ${response.status}.`);
      const data = await response.json();
      if (!data || !Array.isArray(data.papers)) throw new Error('The metadata file does not contain a paper collection.');
      state.data = data;
      const tau = Number(data.ranking?.exposure_tau_days);
      if (Number.isFinite(tau) && tau > 0) state.exposureTauDays = tau;
      if ($('ranking-tau')) $('ranking-tau').value = state.exposureTauDays;
      if (Array.isArray(data.categories) && data.categories.length) state.categories = data.categories.map(c => ({ ...c, badge: DEFAULT_CATEGORIES.find(d => d.id === c.id)?.badge }));
      state.papers = data.papers.filter(p => p && p.id && p.title).map(p => ({ ...p, id: String(p.id), title: String(p.title), abstract: String(p.abstract || ''), source: sourceKey(p.source), categories: Array.isArray(p.categories) ? p.categories : [], authors: Array.isArray(p.authors) ? p.authors.map(a => normalizeAuthor(typeof a === 'string' ? a : a.name)).filter(Boolean) : [], tags: Array.isArray(p.tags) ? p.tags : [] }));
      initializeFilters(); renderSnapshot(); render();
    } catch (error) {
      $('result-count').textContent = 'Collection unavailable';
      $('snapshot-window').textContent = 'The metadata could not be loaded.';
      $('coverage-summary').replaceChildren(element('p', 'coverage-incomplete', 'Source coverage is unavailable until the metadata loads.'));
      $('ranking-note').textContent = 'Serve this page through a local web server so the metadata file can be read.';
      const empty = element('div', 'empty-state');
      empty.append(element('h2', '', 'The collection could not be opened'), element('p', '', error.message));
      const retry = element('button', 'outline-button', 'Try again'); retry.type = 'button'; retry.addEventListener('click', () => location.reload()); empty.append(retry);
      $('paper-results').replaceChildren(empty); $('paper-results').setAttribute('aria-busy', 'false');
    }
  }
  load();
})();
