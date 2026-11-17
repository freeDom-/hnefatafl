import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assessAudit, collectAdvisories } from './auditDependencies.mjs';

const lockPackages = {
  'node_modules/picomatch': { dev: true },
  'node_modules/fdir': { dev: true },
  'node_modules/tinyglobby': { dev: true },
  'node_modules/vite': { dev: true },
  'node_modules/vitest': { dev: true },
};
const exception = {
  advisory: 'GHSA-FC5Q-39XC-VJPF',
  package: 'picomatch',
  severity: 'high',
  scope: 'development',
  expires: '2026-12-17',
  approvedBy: 'owner',
  approvedAt: '2026-11-17',
  rationale: 'Development-only tooling path with no untrusted glob input.',
};
const exceptions = { schema: 1, exceptions: [exception] };
const clean = { vulnerabilities: {}, metadata: { vulnerabilities: { total: 0 } } };
const audit = {
  metadata: { vulnerabilities: { total: 2, high: 2 } },
  vulnerabilities: {
    picomatch: {
      name: 'picomatch',
      severity: 'high',
      via: [
        {
          url: 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf',
          name: 'picomatch',
          severity: 'high',
          range: '<=4.0.7',
          title: 'ReDoS',
        },
      ],
      effects: ['vite'],
    },
    vite: { name: 'vite', severity: 'high', via: ['picomatch'], effects: [] },
  },
};

test('accepts the exact unexpired development-only advisory with a clean production audit', () => {
  const verdict = assessAudit({
    report: audit,
    production: clean,
    exceptions,
    lockPackages,
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, true, verdict.problems.join('; '));
  assert.deepEqual(verdict.accepted[0].affected, ['picomatch', 'vite']);
});

test('collects each advisory once even when dependents repeat it', () => {
  assert.deepEqual(collectAdvisories(audit), [
    {
      advisory: 'GHSA-FC5Q-39XC-VJPF',
      package: 'picomatch',
      severity: 'high',
      title: 'ReDoS',
      range: '<=4.0.7',
    },
  ]);
});

test('rejects any additional advisory', () => {
  const extra = structuredClone(audit);
  extra.vulnerabilities.other = {
    name: 'other',
    severity: 'moderate',
    via: [
      {
        url: 'https://github.com/advisories/GHSA-AAAA-BBBB-CCCC',
        name: 'other',
        severity: 'moderate',
        range: '<1.0.0',
        title: 'Other issue',
      },
    ],
    effects: [],
  };
  const verdict = assessAudit({
    report: extra,
    production: clean,
    exceptions,
    lockPackages,
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Unaccepted advisory GHSA-AAAA-BBBB-CCCC/);
});

test('rejects an accepted advisory when any affected package is a runtime dependency', () => {
  const verdict = assessAudit({
    report: audit,
    production: clean,
    exceptions,
    lockPackages: { ...lockPackages, 'node_modules/vite': { dev: false } },
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /non-development packages: vite/);
});

test('rejects an expired exception', () => {
  const verdict = assessAudit({
    report: audit,
    production: clean,
    exceptions,
    lockPackages,
    today: '2026-12-18',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /expired on 2026-12-17/);
});

test('rejects a stale exception that no longer matches an advisory', () => {
  const verdict = assessAudit({
    report: clean,
    production: clean,
    exceptions,
    lockPackages,
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /is unused/);
});

test('rejects production audit findings even when a dev exception exists', () => {
  const verdict = assessAudit({
    report: audit,
    production: audit,
    exceptions,
    lockPackages,
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Production-only audit/);
});

test('rejects an exception without approval metadata', () => {
  const incomplete = {
    schema: 1,
    exceptions: [{ ...exception, approvedBy: '', rationale: '' }],
  };
  const verdict = assessAudit({
    report: audit,
    production: clean,
    exceptions: incomplete,
    lockPackages,
    today: '2026-11-17',
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /needs rationale, approver and approval date/);
});
