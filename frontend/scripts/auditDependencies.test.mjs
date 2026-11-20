import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assessAudit, collectAdvisories, parseAuditOutput } from './auditDependencies.mjs';

const emptyAudit = () => ({
  auditReportVersion: 2,
  vulnerabilities: {},
  metadata: {
    vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 },
    dependencies: { prod: 89, dev: 207, optional: 53, peer: 8, total: 295 },
  },
});

const advisory = {
  source: 1247710,
  name: 'picomatch',
  dependency: 'picomatch',
  title: 'picomatch vulnerable to ReDoS through single-branch repeated extglobs',
  url: 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf',
  severity: 'high',
  range: '<=4.0.7',
};

function makeAudit(vulnerabilities) {
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const vulnerability of Object.values(vulnerabilities)) counts[vulnerability.severity]++;
  return {
    auditReportVersion: 2,
    vulnerabilities,
    metadata: {
      vulnerabilities: { ...counts, total: Object.keys(vulnerabilities).length },
      dependencies: { prod: 89, dev: 207, optional: 53, peer: 8, total: 295 },
    },
  };
}

const vulnerabilities = () => ({
  picomatch: {
    name: 'picomatch',
    severity: 'high',
    via: [advisory],
    effects: ['fdir', 'tinyglobby', 'vite', 'vitest'],
  },
  fdir: {
    name: 'fdir',
    severity: 'high',
    via: ['picomatch'],
    effects: ['tinyglobby', 'vite'],
  },
  tinyglobby: {
    name: 'tinyglobby',
    severity: 'high',
    via: ['fdir', 'picomatch'],
    effects: ['vite', 'vitest'],
  },
  vite: { name: 'vite', severity: 'high', via: ['fdir', 'tinyglobby', 'picomatch'], effects: [] },
  vitest: { name: 'vitest', severity: 'high', via: ['picomatch', 'tinyglobby'], effects: [] },
});

const audit = () => makeAudit(vulnerabilities());

const lockPackages = {
  'node_modules/fdir': { version: '6.5.0', dev: true },
  'node_modules/picomatch': { version: '4.0.7', dev: true },
  'node_modules/tinyglobby': { version: '0.2.17', dev: true },
  'node_modules/vite': { version: '7.3.7', dev: true },
  'node_modules/vitest': { version: '4.1.12', dev: true },
};

const baseException = {
  advisory: 'GHSA-fc5q-39xc-vjpf',
  cve: 'CVE-2026-112366',
  package: 'picomatch',
  severity: 'high',
  scope: 'development',
  expires: '2026-12-17',
  advisoryRange: '<=4.0.7',
  approvalReference: 'https://github.com/freeDom-/hnefatafl/pull/95#issuecomment-6976788744',
  affectedPackages: ['fdir', 'picomatch', 'tinyglobby', 'vite', 'vitest'],
  packageVersions: {
    fdir: '6.5.0',
    picomatch: '4.0.7',
    tinyglobby: '0.2.17',
    vite: '7.3.7',
    vitest: '4.1.12',
  },
  rationale:
    'Development-only tooling path with no untrusted glob input and a clean production audit.',
};

const exceptions = () => ({
  schema: 3,
  approvedBy: 'freeDom-',
  approvedAt: '2026-11-18',
  reviewReference: 'https://github.com/freeDom-/hnefatafl/pull/95',
  exceptions: [structuredClone(baseException)],
});

const assess = (overrides = {}) =>
  assessAudit({
    report: audit(),
    production: emptyAudit(),
    exceptions: exceptions(),
    lockPackages,
    today: '2026-11-18',
    ...overrides,
  });

test('accepts the exact unexpired development-only advisory with a clean production audit', () => {
  const verdict = assess();
  assert.equal(verdict.ok, true, verdict.problems.join('; '));
  assert.deepEqual(verdict.accepted[0].affected, [
    'fdir',
    'picomatch',
    'tinyglobby',
    'vite',
    'vitest',
  ]);
});

test('collects each advisory once and keeps non-GitHub advisory identities', () => {
  assert.deepEqual(
    collectAdvisories(audit()).map((entry) => [entry.id, entry.package, entry.range]),
    [['GHSA-FC5Q-39XC-VJPF', 'picomatch', '<=4.0.7']],
  );
  const withOtherUrl = makeAudit({
    ...vulnerabilities(),
    other: {
      name: 'other',
      severity: 'high',
      via: [
        {
          name: 'other',
          severity: 'high',
          title: 'Unfamiliar advisory',
          range: '<1.0.0',
          url: 'https://example.test/advisories/CVE-2026-9999',
        },
      ],
      effects: [],
    },
  });
  assert.ok(
    collectAdvisories(withOtherUrl).some(
      (entry) => entry.package === 'other' && entry.id.startsWith('URL-'),
    ),
  );
});

test('fails when a second vulnerability uses a non-GitHub advisory URL', () => {
  const report = makeAudit({
    ...vulnerabilities(),
    other: {
      name: 'other',
      severity: 'high',
      via: [
        {
          name: 'other',
          severity: 'high',
          title: 'Unfamiliar advisory',
          range: '<1.0.0',
          url: 'https://example.test/advisories/CVE-2026-9999',
        },
      ],
      effects: [],
    },
  });
  const verdict = assess({ report });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Unaccepted advisory URL-/);
  assert.match(verdict.problems.join(' '), /other is not attributed/);
});

test('fails when an additional vulnerable package has only string-based via entries', () => {
  const report = makeAudit({
    ...vulnerabilities(),
    ghost: {
      name: 'ghost',
      severity: 'high',
      via: ['not-reported-anywhere'],
      effects: [],
    },
  });
  const verdict = assess({ report });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /references unknown package not-reported-anywhere/);
  assert.match(verdict.problems.join(' '), /ghost is not attributed/);
});

test('fails closed for a new string-seeded dependent of the accepted advisory', () => {
  const report = makeAudit({
    ...vulnerabilities(),
    ghost: { name: 'ghost', severity: 'high', via: ['picomatch'], effects: [] },
  });
  const verdict = assess({ report });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /affected packages changed/);
});

test('fails when advisory metadata is missing or malformed', () => {
  const missingUrl = makeAudit({
    picomatch: {
      name: 'picomatch',
      severity: 'high',
      via: [{ name: 'picomatch', severity: 'high', title: 'No url', range: '<=4.0.7' }],
      effects: [],
    },
  });
  let verdict = assess({ report: missingUrl });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /no usable URL or source/);

  const missingRange = makeAudit({
    picomatch: {
      name: 'picomatch',
      severity: 'high',
      via: [{ ...advisory, range: '' }],
      effects: [],
    },
  });
  verdict = assess({ report: missingRange });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /no affected range/);
});

test('fails when the audit response contains an error', () => {
  assert.throws(
    () =>
      parseAuditOutput({
        status: 1,
        stdout: JSON.stringify({ error: { code: 'ENOLOCK', summary: 'no lockfile' } }),
      }),
    /reported an error: no lockfile/,
  );
});

test('fails when the audit command fails unexpectedly', () => {
  assert.throws(() => parseAuditOutput({ status: 2, stdout: '', stderr: 'boom' }), /status 2/);
  assert.throws(
    () => parseAuditOutput({ status: null, error: new Error('spawn failed') }),
    /could not run: spawn failed/,
  );
  assert.throws(
    () => parseAuditOutput({ status: 1, signal: 'SIGTERM', stdout: '' }),
    /terminated by SIGTERM/,
  );
  assert.throws(() => parseAuditOutput({ status: 1, stdout: 'not json' }), /did not return JSON/);
  assert.throws(
    () => parseAuditOutput({ status: 0, stdout: JSON.stringify({ auditReportVersion: 1 }) }),
    /unsupported auditReportVersion/,
  );
});

test('fails when metadata reports more vulnerabilities than were accounted for', () => {
  const report = audit();
  report.metadata.vulnerabilities.total = 6;
  const verdict = assess({ report });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /metadata.total reports 6/);
});

test('fails when an accepted advisory reaches a runtime dependency', () => {
  const verdict = assess({
    lockPackages: { ...lockPackages, 'node_modules/vite': { version: '7.3.7', dev: false } },
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /non-development packages: vite/);
});

test('fails for expired, re-reviewed or no-longer-applicable exceptions', () => {
  let verdict = assess({ today: '2026-12-18' });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /expired on 2026-12-17/);

  const changedSeverity = exceptions();
  changedSeverity.exceptions[0].severity = 'moderate';
  verdict = assess({ exceptions: changedSeverity });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /severity changed from moderate to high/);

  const changedRange = exceptions();
  changedRange.exceptions[0].advisoryRange = '<=4.0.6';
  verdict = assess({ exceptions: changedRange });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /range changed from <=4.0.6 to <=4.0.7/);

  const changedVersion = exceptions();
  changedVersion.exceptions[0].packageVersions.picomatch = '4.0.6';
  verdict = assess({ exceptions: changedVersion });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /reviewed picomatch@4.0.6/);

  verdict = assess({ report: emptyAudit() });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /is unused/);
});

test('fails for invalid approval metadata, future dates and overlong acceptance windows', () => {
  const badApprover = exceptions();
  badApprover.approvedBy = '';
  assert.match(assess({ exceptions: badApprover }).problems.join(' '), /GitHub-handle approvedBy/);

  const badDates = exceptions();
  badDates.approvedAt = '2026-13-01';
  assert.match(assess({ exceptions: badDates }).problems.join(' '), /valid ISO approvedAt/);

  const future = exceptions();
  future.approvedAt = '2026-11-19';
  assert.match(assess({ exceptions: future }).problems.join(' '), /cannot be in the future/);

  const tooLong = exceptions();
  tooLong.exceptions[0].expires = '2027-03-01';
  assert.match(assess({ exceptions: tooLong }).problems.join(' '), /maximum is 90/);

  const badReview = exceptions();
  badReview.reviewReference = 'http://insecure.test';
  assert.match(assess({ exceptions: badReview }).problems.join(' '), /https reviewReference/);

  const unknownField = exceptions();
  unknownField.exceptions[0].exclude = true;
  assert.match(
    assess({ exceptions: unknownField }).problems.join(' '),
    /Unknown exception field exclude/,
  );

  const noRationale = exceptions();
  noRationale.exceptions[0].rationale = 'short';
  assert.match(assess({ exceptions: noRationale }).problems.join(' '), /substantive rationale/);
});

test('fails when the approval reference is missing, malformed or not durable', () => {
  const missing = exceptions();
  delete missing.exceptions[0].approvalReference;
  let verdict = assess({ exceptions: missing });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /needs an https approvalReference/);

  const insecure = exceptions();
  insecure.exceptions[0].approvalReference = 'http://github.com/freeDom-/hnefatafl/pull/95';
  verdict = assess({ exceptions: insecure });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /needs an https approvalReference/);

  const external = exceptions();
  external.exceptions[0].approvalReference = 'https://example.test/approval';
  verdict = assess({ exceptions: external });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /durable record in this repository/);
});

test('fails when production audit findings are present or the report is malformed', () => {
  let verdict = assess({ production: audit() });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Production-only audit reports 5/);

  verdict = assess({ production: {} });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Production-only audit report is malformed/);

  verdict = assess({ report: {} });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /no vulnerability map/);
});

const emptyExceptions = () => ({ schema: 3, exceptions: [] });

test('passes clean full and production audits with an empty exception list', () => {
  const verdict = assess({
    report: emptyAudit(),
    production: emptyAudit(),
    exceptions: emptyExceptions(),
  });
  assert.equal(verdict.ok, true, verdict.problems.join('; '));
  assert.deepEqual(verdict.accepted, []);
  assert.deepEqual(verdict.advisories, []);
  assert.equal(verdict.productionClean, true);
});

test('passes a clean audit with no active approval metadata', () => {
  const verdict = assess({
    report: emptyAudit(),
    production: emptyAudit(),
    exceptions: { schema: 3, notes: 'no active exceptions', exceptions: [] },
  });
  assert.equal(verdict.ok, true, verdict.problems.join('; '));
  assert.deepEqual(verdict.accepted, []);
});

test('fails a new vulnerability when the exception list is empty', () => {
  const verdict = assess({
    report: audit(),
    production: emptyAudit(),
    exceptions: emptyExceptions(),
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Unaccepted advisory GHSA-FC5Q-39XC-VJPF/);
  assert.match(verdict.problems.join(' '), /picomatch is not attributed/);
});

test('fails a production vulnerability when the exception list is empty', () => {
  const verdict = assess({
    report: emptyAudit(),
    production: audit(),
    exceptions: emptyExceptions(),
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Production-only audit reports 5/);
});

test('fails a missing, non-array or unsupported exception configuration', () => {
  let verdict = assess({ report: emptyAudit(), exceptions: { schema: 3 } });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /needs an exceptions array/);

  verdict = assess({ report: emptyAudit(), exceptions: { schema: 3, exceptions: {} } });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /needs an exceptions array/);

  verdict = assess({ report: emptyAudit(), exceptions: { schema: 2, exceptions: [] } });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /schema-3 exception file/);

  verdict = assess({ report: emptyAudit(), exceptions: { schema: 3, exceptions: [], extra: 1 } });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /Unknown top-level exception field extra/);
});

test('fails a stale exception when the audit is clean', () => {
  const verdict = assess({ report: emptyAudit(), production: emptyAudit() });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /is unused/);
});

test('requires file-level approval metadata while an exception is active', () => {
  const noApprover = exceptions();
  delete noApprover.approvedBy;
  let verdict = assess({ exceptions: noApprover });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /GitHub-handle approvedBy/);

  const noDate = exceptions();
  delete noDate.approvedAt;
  verdict = assess({ exceptions: noDate });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /valid ISO approvedAt/);

  const noReview = exceptions();
  delete noReview.reviewReference;
  verdict = assess({ exceptions: noReview });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /https reviewReference/);
});

test('rejects malformed approval metadata even when no exception is active', () => {
  const badHandle = { schema: 3, approvedBy: 'not a handle', exceptions: [] };
  let verdict = assess({ report: emptyAudit(), exceptions: badHandle });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /approvedBy must be a GitHub handle/);

  const badDate = { schema: 3, approvedAt: 'not-a-date', exceptions: [] };
  verdict = assess({ report: emptyAudit(), exceptions: badDate });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /approvedAt must be a valid ISO date/);

  const badReview = { schema: 3, reviewReference: 'http://insecure.test', exceptions: [] };
  verdict = assess({ report: emptyAudit(), exceptions: badReview });
  assert.equal(verdict.ok, false);
  assert.match(verdict.problems.join(' '), /reviewReference must be an https URL/);
});

test('rejects non-string approvedBy values for active and empty exception lists', () => {
  const values = [
    ['array', ['freeDom-']],
    ['number', 123],
    ['boolean', true],
    ['null', null],
    ['object', { handle: 'freeDom-' }],
  ];
  for (const [label, value] of values) {
    const active = exceptions();
    active.approvedBy = value;
    const activeVerdict = assess({ exceptions: active });
    assert.equal(activeVerdict.ok, false, `active approvedBy ${label} must fail`);
    assert.match(activeVerdict.problems.join(' '), /approvedBy/);

    const empty = { schema: 3, approvedBy: value, exceptions: [] };
    const emptyVerdict = assess({ report: emptyAudit(), exceptions: empty });
    assert.equal(emptyVerdict.ok, false, `empty approvedBy ${label} must fail`);
    assert.match(emptyVerdict.problems.join(' '), /approvedBy/);
  }
});

test('rejects non-string advisory identifiers without implicit coercion', () => {
  const values = [
    ['array', ['GHSA-fc5q-39xc-vjpf']],
    ['number', 123],
    ['boolean', true],
    ['null', null],
    ['coercible object', { toUpperCase: () => 'GHSA-FC5Q-39XC-VJPF' }],
  ];
  for (const [label, value] of values) {
    const config = exceptions();
    config.exceptions[0].advisory = value;
    const verdict = assess({ exceptions: config });
    assert.equal(verdict.ok, false, `advisory ${label} must fail`);
    assert.match(verdict.problems.join(' '), /advisory|Unaccepted/i);
  }
});

test('rejects non-string cve identifiers without implicit coercion', () => {
  const values = [
    ['array', ['CVE-2026-112366']],
    ['number', 2026112366],
    ['boolean', true],
    ['null', null],
    ['object', { id: 'CVE-2026-112366' }],
  ];
  for (const [label, value] of values) {
    const config = exceptions();
    config.exceptions[0].cve = value;
    const verdict = assess({ exceptions: config });
    assert.equal(verdict.ok, false, `cve ${label} must fail`);
    assert.match(verdict.problems.join(' '), /invalid CVE id/);
  }
});
