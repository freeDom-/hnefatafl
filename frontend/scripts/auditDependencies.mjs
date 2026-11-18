#!/usr/bin/env node
/**
 * Dependency audit with explicit, expiring, owner-approved risk acceptances.
 *
 * The full `npm audit` still runs and every reported vulnerable package must be
 * accounted for. A package is accepted only when it belongs to the exact
 * advisory/package recorded in `security/dependency-audit-exceptions.json`, the
 * exception is within its approved window, every affected package is
 * development-only, the reviewed versions and advisory range/severity still
 * match, and the production-only audit is clean.
 *
 * Anything unrecognized fails: additional advisories (including non-GitHub
 * URLs), string-only `via` packages outside the accepted component, malformed
 * entries, incomplete/erroring audit reports, metadata count mismatches, stale
 * exceptions, missing approval metadata and expired exceptions. This is an
 * explicit exception mechanism, not an audit suppression switch.
 *
 * A clean audit needs no acceptance: an empty `exceptions` array is valid and
 * requires no owner approval metadata, because no risk is being accepted. The
 * owner approval metadata is mandatory and strictly validated only while at
 * least one exception is active.
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const exceptionsPath = join(frontend, 'security', 'dependency-audit-exceptions.json');
const lockPath = join(frontend, 'package-lock.json');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const GHSA = /^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$/i;
const CVE = /^CVE-\d{4}-\d{4,}$/i;
const GITHUB_HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const SEVERITIES = new Set(['info', 'low', 'moderate', 'high', 'critical']);
const MAX_ACCEPTANCE_DAYS = 90;

const TOP_LEVEL_KEYS = new Set([
  'schema',
  'approvedBy',
  'approvedAt',
  'approvalReference',
  'reviewReference',
  'exceptions',
  'notes',
]);
const EXCEPTION_KEYS = new Set([
  'advisory',
  'cve',
  'package',
  'severity',
  'scope',
  'expires',
  'rationale',
  'advisoryRange',
  'approvalReference',
  'affectedPackages',
  'packageVersions',
  'paths',
  'notes',
]);

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function validDate(value) {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function validHttpsUrl(value) {
  if (typeof value !== 'string') return false;
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

function ghsaFromUrl(url) {
  if (typeof url !== 'string') return null;
  const match = /GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}/i.exec(url);
  return match ? match[0].toUpperCase() : null;
}

/**
 * Stable identity for an advisory. GHSA when present; otherwise the npm advisory
 * source or URL. An unrecognized URL is never discarded: it produces a non-GHSA
 * id that cannot match an exception and therefore fails as unaccepted.
 */
function advisoryId(via) {
  const ghsa = ghsaFromUrl(via.url);
  if (ghsa) return ghsa;
  if (via.source !== undefined && via.source !== null && `${via.source}` !== '') {
    return `NPM-${via.source}`;
  }
  if (typeof via.url === 'string' && via.url) return `URL-${via.url}`;
  return null;
}

/** Parse and structurally validate one npm audit invocation result. */
export function parseAuditOutput(result, label = 'npm audit') {
  if (result?.error) {
    throw new Error(`${label} could not run: ${result.error.message ?? result.error}`);
  }
  if (result?.signal) {
    throw new Error(`${label} was terminated by ${result.signal}.`);
  }
  if (result?.status !== 0 && result?.status !== 1) {
    throw new Error(`${label} exited with unexpected status ${result?.status}.`);
  }
  const stdout = String(result?.stdout ?? '');
  if (!stdout.trim()) {
    throw new Error(
      `${label} returned no output (status ${result?.status}): ${String(result?.stderr ?? '').slice(0, 300)}`,
    );
  }
  let report;
  try {
    report = JSON.parse(stdout);
  } catch {
    throw new Error(`${label} did not return JSON: ${stdout.slice(0, 200)}`);
  }
  if (report?.error) {
    throw new Error(
      `${label} reported an error: ${report.error.summary ?? report.error.code ?? 'unknown'}`,
    );
  }
  if (report?.auditReportVersion !== 2) {
    throw new Error(
      `${label} returned unsupported auditReportVersion ${report?.auditReportVersion ?? 'missing'}.`,
    );
  }
  if (!isPlainObject(report.vulnerabilities)) {
    throw new Error(`${label} has no vulnerability map.`);
  }
  if (!isPlainObject(report.metadata?.vulnerabilities)) {
    throw new Error(`${label} has no metadata.vulnerabilities counts.`);
  }
  return report;
}

/**
 * Inspect an audit report without deciding whether anything is acceptable.
 * Returns advisories, a package graph and every structural problem found.
 */
export function inspectAudit(report) {
  const problems = [];
  if (!isPlainObject(report?.vulnerabilities)) {
    return {
      problems: ['Audit report has no vulnerability map.'],
      advisories: [],
      packages: new Map(),
    };
  }
  const entries = report.vulnerabilities;
  const packages = new Map();
  const advisories = new Map();

  for (const [name, vulnerability] of Object.entries(entries)) {
    if (!isPlainObject(vulnerability)) {
      problems.push(`Vulnerability entry ${name} is malformed.`);
      continue;
    }
    if (!Array.isArray(vulnerability.via) || vulnerability.via.length === 0) {
      problems.push(`Vulnerability entry ${name} has no via entries.`);
      continue;
    }
    if (!SEVERITIES.has(vulnerability.severity)) {
      problems.push(`Vulnerability entry ${name} has an unsupported severity.`);
    }
    const record = {
      name,
      severity: vulnerability.severity,
      viaStrings: [],
      advisories: [],
      effects: Array.isArray(vulnerability.effects)
        ? vulnerability.effects.filter((effect) => typeof effect === 'string')
        : [],
    };
    for (const via of vulnerability.via) {
      if (typeof via === 'string') {
        record.viaStrings.push(via);
        continue;
      }
      if (!isPlainObject(via)) {
        problems.push(`Vulnerability entry ${name} has a malformed via entry.`);
        continue;
      }
      const id = advisoryId(via);
      const pkg = via.name ?? name;
      const severity = via.severity ?? vulnerability.severity;
      if (!id) problems.push(`Advisory for ${name} has no usable URL or source.`);
      if (typeof pkg !== 'string' || !pkg)
        problems.push(`Advisory for ${name} has no package name.`);
      if (pkg !== name) {
        problems.push(`Advisory package ${pkg} does not match vulnerability entry ${name}.`);
      }
      if (!SEVERITIES.has(severity)) problems.push(`Advisory for ${name} has an invalid severity.`);
      if (typeof via.range !== 'string' || !via.range) {
        problems.push(`Advisory for ${name} has no affected range.`);
      }
      if (typeof via.title !== 'string' || !via.title) {
        problems.push(`Advisory for ${name} has no title.`);
      }
      if (!id) continue;
      const advisory = {
        id,
        ghsa: ghsaFromUrl(via.url),
        package: pkg,
        severity,
        title: via.title,
        range: via.range,
        url: via.url ?? null,
        source: via.source ?? null,
      };
      record.advisories.push(advisory);
      const key = `${advisory.id}:${advisory.package}`;
      if (!advisories.has(key)) advisories.set(key, advisory);
    }
    packages.set(name, record);
  }

  // Every referenced package must be present; a dangling reference means the
  // report is incomplete and cannot be fully accounted for.
  for (const record of packages.values()) {
    for (const reference of [...record.viaStrings, ...record.effects]) {
      if (!packages.has(reference)) {
        problems.push(
          `Vulnerability entry ${record.name} references unknown package ${reference}.`,
        );
      }
    }
  }

  // npm's severity totals must agree with the entries actually reported.
  const metadata = report.metadata?.vulnerabilities;
  if (!isPlainObject(metadata)) {
    problems.push('Audit report has no metadata.vulnerabilities counts.');
  } else {
    const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
    for (const vulnerability of Object.values(entries)) {
      if (isPlainObject(vulnerability) && counts[vulnerability.severity] !== undefined) {
        counts[vulnerability.severity]++;
      }
    }
    for (const severity of Object.keys(counts)) {
      if (metadata[severity] !== counts[severity]) {
        problems.push(
          `metadata.${severity} reports ${metadata[severity]} but ${counts[severity]} entries were found.`,
        );
      }
    }
    if (metadata.total !== Object.keys(entries).length) {
      problems.push(
        `metadata.total reports ${metadata.total} but ${Object.keys(entries).length} entries were found.`,
      );
    }
  }

  return { problems, advisories: [...advisories.values()], packages };
}

/** Unique advisories reported anywhere in an npm audit JSON document. */
export function collectAdvisories(report) {
  return inspectAudit(report).advisories;
}

/** Every package npm considers part of an advisory's affected component. */
export function affectedPackages(report, rootPackage) {
  return componentFor(inspectAudit(report).packages, rootPackage);
}

function componentFor(packages, rootPackage) {
  if (!packages.has(rootPackage)) return [];
  const dependents = new Map();
  for (const record of packages.values()) {
    for (const reference of record.viaStrings) {
      if (!dependents.has(reference)) dependents.set(reference, []);
      dependents.get(reference).push(record.name);
    }
  }
  const seen = new Set([rootPackage]);
  const queue = [rootPackage];
  while (queue.length) {
    const name = queue.shift();
    const record = packages.get(name);
    const next = [...(record?.effects ?? []), ...(dependents.get(name) ?? [])];
    for (const dependent of next) {
      if (packages.has(dependent) && !seen.has(dependent)) {
        seen.add(dependent);
        queue.push(dependent);
      }
    }
  }
  return [...seen].sort();
}

function entriesFor(lockPackages, name) {
  return Object.entries(lockPackages ?? {}).filter(
    ([key]) => key === `node_modules/${name}` || key.endsWith(`/node_modules/${name}`),
  );
}

function validateExceptionFile(exceptions, today) {
  const problems = [];
  if (!isPlainObject(exceptions) || exceptions.schema !== 3) {
    return ['security/dependency-audit-exceptions.json must be a schema-3 exception file.'];
  }
  for (const key of Object.keys(exceptions)) {
    if (!TOP_LEVEL_KEYS.has(key)) problems.push(`Unknown top-level exception field ${key}.`);
  }
  // A missing or non-array `exceptions` field is a malformed configuration and
  // always fails, even when the audits are clean.
  if (!Array.isArray(exceptions.exceptions)) {
    problems.push('Exception file needs an exceptions array.');
    return problems;
  }
  const accepting = exceptions.exceptions.length > 0;
  if (accepting) {
    // Owner approval is mandatory while any risk is being accepted.
    if (!GITHUB_HANDLE.test(exceptions.approvedBy ?? '')) {
      problems.push('Exception file needs a GitHub-handle approvedBy value.');
    }
    if (!validDate(exceptions.approvedAt ?? '')) {
      problems.push('Exception file needs a valid ISO approvedAt date.');
    } else if (exceptions.approvedAt > today) {
      problems.push('Exception approvedAt cannot be in the future.');
    }
    if (!validHttpsUrl(exceptions.reviewReference)) {
      problems.push('Exception file needs an https reviewReference.');
    }
  } else {
    // No risk is accepted, so approval metadata is not required. If it is still
    // present it must remain well formed rather than silently ignored.
    if (exceptions.approvedBy !== undefined && !GITHUB_HANDLE.test(exceptions.approvedBy ?? '')) {
      problems.push('Exception approvedBy must be a GitHub handle when present.');
    }
    if (exceptions.approvedAt !== undefined) {
      if (!validDate(exceptions.approvedAt ?? '')) {
        problems.push('Exception approvedAt must be a valid ISO date when present.');
      } else if (exceptions.approvedAt > today) {
        problems.push('Exception approvedAt cannot be in the future.');
      }
    }
    if (exceptions.reviewReference !== undefined && !validHttpsUrl(exceptions.reviewReference)) {
      problems.push('Exception reviewReference must be an https URL when present.');
    }
  }
  if (exceptions.approvalReference !== undefined && !validHttpsUrl(exceptions.approvalReference)) {
    problems.push('Exception approvalReference must be an https URL when present.');
  }
  return problems;
}

function validateExceptionEntry(entry, exceptions, today) {
  const problems = [];
  if (!isPlainObject(entry)) return ['Exception entry is malformed.'];
  for (const key of Object.keys(entry)) {
    if (!EXCEPTION_KEYS.has(key)) problems.push(`Unknown exception field ${key}.`);
  }
  if (!GHSA.test(entry.advisory ?? '')) problems.push('Exception advisory must be a GHSA id.');
  if (entry.cve !== undefined && !CVE.test(entry.cve)) {
    problems.push(`Exception ${entry.advisory} has an invalid CVE id.`);
  }
  if (typeof entry.package !== 'string' || !entry.package) {
    problems.push(`Exception ${entry.advisory} needs a package name.`);
  }
  if (!SEVERITIES.has(entry.severity)) {
    problems.push(`Exception ${entry.advisory} needs a valid severity.`);
  }
  if (entry.scope !== 'development') {
    problems.push(`Exception ${entry.advisory} must be scoped to development.`);
  }
  if (!validDate(entry.expires ?? '')) {
    problems.push(`Exception ${entry.advisory} needs a valid ISO expiry date.`);
  }
  if (!validDate(exceptions.approvedAt ?? '')) {
    // The file-level problem is already reported; avoid duplicate date math.
    return problems;
  }
  if (validDate(entry.expires ?? '')) {
    if (entry.expires < today) {
      problems.push(`Exception ${entry.advisory} expired on ${entry.expires}.`);
    }
    if (entry.expires < exceptions.approvedAt) {
      problems.push(`Exception ${entry.advisory} expires before it was approved.`);
    }
    const days =
      (Date.parse(`${entry.expires}T00:00:00Z`) -
        Date.parse(`${exceptions.approvedAt}T00:00:00Z`)) /
      86400000;
    if (days > MAX_ACCEPTANCE_DAYS) {
      problems.push(
        `Exception ${entry.advisory} spans ${days} days; the maximum is ${MAX_ACCEPTANCE_DAYS}.`,
      );
    }
  }
  if (typeof entry.rationale !== 'string' || entry.rationale.trim().length < 40) {
    problems.push(`Exception ${entry.advisory} needs a substantive rationale.`);
  }
  if (typeof entry.advisoryRange !== 'string' || !entry.advisoryRange) {
    problems.push(`Exception ${entry.advisory} needs the reviewed advisoryRange.`);
  }
  if (!validHttpsUrl(entry.approvalReference)) {
    problems.push(`Exception ${entry.advisory} needs an https approvalReference.`);
  } else if (!/^https:\/\/github\.com\/freeDom-\/hnefatafl\//i.test(entry.approvalReference)) {
    problems.push(
      `Exception ${entry.advisory} approvalReference must be a durable record in this repository.`,
    );
  }
  if (
    !Array.isArray(entry.affectedPackages) ||
    entry.affectedPackages.length === 0 ||
    entry.affectedPackages.some((name) => typeof name !== 'string' || !name) ||
    new Set(entry.affectedPackages).size !== entry.affectedPackages.length
  ) {
    problems.push(`Exception ${entry.advisory} needs a unique affectedPackages list.`);
  }
  if (
    !isPlainObject(entry.packageVersions) ||
    Object.entries(entry.packageVersions).some(
      ([name, version]) => !name || typeof version !== 'string' || !version,
    )
  ) {
    problems.push(`Exception ${entry.advisory} needs exact packageVersions.`);
  }
  if (entry.paths !== undefined) {
    if (!Array.isArray(entry.paths) || entry.paths.some((path) => typeof path !== 'string')) {
      problems.push(`Exception ${entry.advisory} paths must be strings.`);
    }
  }
  return problems;
}

function productionFindings(report) {
  return isPlainObject(report?.vulnerabilities) ? Object.keys(report.vulnerabilities).length : -1;
}

/**
 * Pure decision function used by the CLI and unit tests.
 * `today` is an ISO UTC date so expiry is deterministic in tests.
 */
export function assessAudit({
  report,
  production,
  exceptions,
  lockPackages,
  today = new Date().toISOString().slice(0, 10),
}) {
  const problems = [...validateExceptionFile(exceptions, today)];
  const accepted = [];
  const configured = Array.isArray(exceptions?.exceptions) ? exceptions.exceptions : [];
  const used = new Set();

  const inspected = inspectAudit(report);
  problems.push(...inspected.problems);
  if (productionFindings(production) > 0) {
    problems.push(
      `Production-only audit reports ${productionFindings(production)} affected package(s); runtime findings are never accepted.`,
    );
  } else if (productionFindings(production) < 0) {
    problems.push('Production-only audit report is malformed.');
  }
  const productionInspected = inspectAudit(production);
  problems.push(...productionInspected.problems.map((problem) => `Production audit: ${problem}`));

  const acceptedPackages = new Set();
  for (const advisory of inspected.advisories) {
    const exception = configured.find(
      (entry) =>
        isPlainObject(entry) &&
        entry.advisory?.toUpperCase() === advisory.id.toUpperCase() &&
        entry.package === advisory.package,
    );
    if (!exception) {
      problems.push(
        `Unaccepted advisory ${advisory.id} (${advisory.package}, ${advisory.severity}, ${advisory.range}).`,
      );
      continue;
    }
    used.add(exception);
    const entryProblems = validateExceptionEntry(exception, exceptions, today);
    if (entryProblems.length) {
      problems.push(...entryProblems);
      continue;
    }
    if (exception.severity !== advisory.severity) {
      problems.push(
        `Exception ${advisory.id} severity changed from ${exception.severity} to ${advisory.severity}; re-review required.`,
      );
      continue;
    }
    if (exception.advisoryRange !== advisory.range) {
      problems.push(
        `Exception ${advisory.id} range changed from ${exception.advisoryRange} to ${advisory.range}; re-review required.`,
      );
      continue;
    }
    const affected = componentFor(inspected.packages, advisory.package);
    const expected = [...exception.affectedPackages].sort();
    if (affected.join(',') !== expected.join(',')) {
      problems.push(
        `Exception ${advisory.id} affected packages changed (reviewed ${expected.join(', ')}, now ${affected.join(', ')}); re-review required.`,
      );
      continue;
    }
    const runtime = affected.filter((name) => {
      const entries = entriesFor(lockPackages, name);
      return entries.length === 0 || entries.some(([, entry]) => entry.dev !== true);
    });
    if (runtime.length) {
      problems.push(
        `Exception ${advisory.id} reaches non-development packages: ${runtime.join(', ')}.`,
      );
      continue;
    }
    const versionProblems = [];
    const reviewedVersions = Object.keys(exception.packageVersions).sort();
    if (reviewedVersions.join(',') !== expected.join(',')) {
      versionProblems.push(
        `Exception ${advisory.id} must pin every affected package version (${expected.join(', ')}).`,
      );
    }
    for (const name of affected) {
      const entries = entriesFor(lockPackages, name);
      const versions = new Set(entries.map(([, entry]) => entry.version));
      if (versions.size !== 1) {
        versionProblems.push(`Lockfile has no single resolved version for ${name}.`);
      } else if (exception.packageVersions[name] !== [...versions][0]) {
        versionProblems.push(
          `Exception ${advisory.id} reviewed ${name}@${exception.packageVersions[name]}, lockfile has ${[...versions][0]}; re-review required.`,
        );
      }
    }
    if (versionProblems.length) {
      problems.push(...versionProblems);
      continue;
    }
    accepted.push({ ...advisory, affected, exception });
    for (const name of affected) acceptedPackages.add(name);
  }

  // Every vulnerable package must be attributable to an accepted advisory,
  // including entries that only have string-based `via` references.
  for (const name of inspected.packages.keys()) {
    if (!acceptedPackages.has(name)) {
      problems.push(`Vulnerable package ${name} is not attributed to an accepted advisory.`);
    }
  }

  for (const exception of configured) {
    if (!used.has(exception)) {
      problems.push(
        `Exception ${exception.advisory} (${exception.package}) is unused; remove it after the fix.`,
      );
    }
  }

  return {
    ok: problems.length === 0,
    problems,
    accepted,
    advisories: inspected.advisories,
    productionClean: productionFindings(production) === 0,
  };
}

function runAudit(extra = []) {
  const result = spawnSync(npm, ['audit', '--package-lock-only', '--json', ...extra], {
    cwd: frontend,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  return parseAuditOutput(result, `npm audit ${extra.join(' ')}`.trim());
}

function main() {
  const report = runAudit();
  const production = runAudit(['--omit=dev']);
  const exceptions = JSON.parse(readFileSync(exceptionsPath, 'utf8'));
  const lock = JSON.parse(readFileSync(lockPath, 'utf8'));
  const verdict = assessAudit({
    report,
    production,
    exceptions,
    lockPackages: lock.packages,
  });

  const totals = report.metadata?.vulnerabilities ?? {};
  console.log(
    `npm audit: ${totals.total ?? '?'} affected package(s) ` +
      `(critical ${totals.critical ?? 0}, high ${totals.high ?? 0}, ` +
      `moderate ${totals.moderate ?? 0}, low ${totals.low ?? 0}).`,
  );
  for (const advisory of verdict.advisories) {
    const accepted = verdict.accepted.find(
      (entry) => entry.id === advisory.id && entry.package === advisory.package,
    );
    console.log(
      `  ${accepted ? 'ACCEPTED' : 'UNACCEPTED'} ${advisory.id} ` +
        `${advisory.package} (${advisory.severity}, ${advisory.range}): ${advisory.title}`,
    );
  }
  const published = report.metadata?.dependencies ?? {};
  console.log(
    `Production-only audit: ${verdict.productionClean ? 'clean' : 'FINDINGS'} ` +
      `(dev dependencies in full audit: ${published.dev ?? 'unknown'}).`,
  );

  for (const entry of verdict.accepted) {
    console.error(
      `RISK ACCEPTANCE ACTIVE: ${entry.id} (${entry.package}, ${entry.severity}).\n` +
        `  packages: ${entry.affected.map((name) => `${name}@${entry.exception.packageVersions[name]}`).join(', ')}\n` +
        `  scope: development only\n` +
        `  approved by: ${entry.exception.approvedBy ?? exceptions.approvedBy} on ${entry.exception.approvedAt ?? exceptions.approvedAt}\n` +
        `  review: ${entry.exception.reviewReference ?? exceptions.reviewReference ?? 'n/a'}\n` +
        `  approval: ${entry.exception.approvalReference}\n` +
        `  expires: ${entry.exception.expires}\n` +
        `  rationale: ${entry.exception.rationale}\n` +
        `  This is a temporary, owner-approved exception, not a technical fix. Remove it when upstream ships a patched release.`,
    );
  }

  if (!verdict.ok) {
    for (const problem of verdict.problems) console.error(`AUDIT FAILURE: ${problem}`);
    process.exitCode = 1;
    return;
  }
  console.log(
    verdict.accepted.length
      ? 'Dependency audit accepted the configured exception(s); all other checks passed.'
      : 'Dependency audit clean; no exceptions used.',
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(`AUDIT FAILURE: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
