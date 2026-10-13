'use strict';

// Exercise the shipped browser scoring functions without a browser or DOM mock.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'library/library.js'), 'utf8');
function shippedFunction(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.notEqual(start, -1, `Missing shipped function ${name}`);
  const end = source.indexOf('\n  }', start);
  return source.slice(start, end + 4);
}
const context = vm.createContext({ state: { exposureTauDays: 7 } });
vm.runInContext(['validDate', 'completeVersionScope', 'metricAgeDays', 'metricScore'].map(shippedFunction).join('\n'), context);
const score = (paper, kind = 'views') => context.metricScore(paper, kind);
const fixture = (age, count = 100) => ({ published: '2026-09-01', metrics: {
  first_posted_at: '2026-09-01T12:00:00Z', observed_at: new Date(Date.parse('2026-09-01T12:00:00Z') + age * 86400000).toISOString(),
  views: count, downloads: count / 2, scope: 'all-versions', coverage_complete: true
} });
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) <= 1e-9 * Math.max(1, Math.abs(expected)), `${actual} != ${expected}`);
// Python preserves source microseconds; JavaScript Date truncates to milliseconds.
// Bound that known timestamp resolution instead of relaxing all scoring tests.
const closeWithTimestampResolution = (paper, kind, actual, stored) => {
  const age = paper.metrics.age_days, browserAge = context.metricAgeDays(paper);
  const resolutionDays = 1 / 86400000;
  assert.ok(Math.abs(age - browserAge) <= resolutionDays + 1e-12, `Age precision mismatch: ${paper.id}`);
  const count = paper.metrics[kind], tau = context.state.exposureTauDays;
  const low = count / -Math.expm1(-Math.max(1, age + resolutionDays) / tau);
  const high = count / -Math.expm1(-Math.max(1, age - resolutionDays) / tau);
  close(stored, count / -Math.expm1(-age / tau));
  const roundoff = 1e-9 * Math.max(1, Math.abs(stored));
  assert.ok(actual >= low - roundoff && actual <= high + roundoff, `Score differs beyond millisecond precision: ${paper.id}`);
};
let checked = 0;
const check = fn => { fn(); checked += 1; };
check(() => close(score(fixture(7)), 100 / (1 - Math.exp(-1))));
check(() => { for (const age of [1, 2, 7, 14, 30, 90]) close(score(fixture(age, 500 * (1 - Math.exp(-age / 7)))), 500); });
check(() => close(score(fixture(300)), 100));
check(() => assert.ok(score(fixture(1)) > score(fixture(7)) && score(fixture(7)) > score(fixture(30))));
check(() => close(score(fixture(0.1)), score(fixture(1))));
check(() => close(context.metricAgeDays(fixture(1.5)), 1.5));
check(() => assert.equal(score(fixture(10, 0)), 0));
check(() => { const paper = fixture(10); paper.metrics.views = null; assert.equal(score(paper), null); });
check(() => { const paper = fixture(10); paper.metrics.scope = 'partial'; assert.equal(score(paper), null); });
check(() => { const paper = fixture(10); paper.metrics.coverage_complete = false; assert.equal(score(paper), null); });
check(() => assert.equal(score(fixture(-1)), null));
check(() => { const paper = fixture(10); paper.metrics.observed_at = 'invalid'; assert.equal(score(paper), null); });
check(() => { for (const tau of [0, -1, NaN, Infinity]) { context.state.exposureTauDays = tau; assert.equal(score(fixture(7)), null); } context.state.exposureTauDays = 7; });
check(() => { const base = score(fixture(2)); context.state.exposureTauDays = 14; assert.ok(score(fixture(2)) > base); context.state.exposureTauDays = 7; });
check(() => close(score(fixture(7), 'downloads'), score(fixture(7)) / 2));
check(() => { const paper = fixture(7); paper.updated = '2026-10-12'; close(score(paper), score(fixture(7))); });
const data = JSON.parse(fs.readFileSync(path.join(root, 'library/data.json'), 'utf8'));
assert.equal(data.ranking.model, 'exponential-saturation');
context.state.exposureTauDays = data.ranking.exposure_tau_days;
let measured = 0;
for (const paper of data.papers) {
  for (const kind of ['views', 'downloads']) {
    const value = score(paper, kind), stored = paper.metrics[`${kind}_age_adjusted`];
    if (value === null) assert.equal(stored, null, `Unavailable ${paper.id} assigned a score`);
    else { closeWithTimestampResolution(paper, kind, value, stored); if (kind === 'views') measured += 1; }
  }
}
console.log(`${checked} browser scoring checks passed; ${measured} measured papers match backend exponential scores.`);
