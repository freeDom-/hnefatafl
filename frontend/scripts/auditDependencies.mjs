#!/usr/bin/env node
/**
 * Dependency audit with explicit, expiring, owner-approved risk acceptances.
 *
 * The full `npm audit` still runs and is printed. The command fails for every
 * advisory except the exact GHSA/package pairs recorded in
 * `security/dependency-audit-exceptions.json`, and only while that exception is
 * unexpired, scoped to development-only packages and paired with a clean
 * production-only audit. Any new advisory, a production finding, a non-dev
 * affected package, a stale exception, malformed data or an expired exception
 * fails the check.
 *
 * The exception file is deliberately small and reviewable; it is not an audit
 * suppression switch.
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const exceptionsPath = join(frontend, 'security', 'dependency-audit-exceptions.json');
const lockPath = join(frontend, 'package-lock.json');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

function runAudit(extra = []) {
  const result = spawnSync(npm, ['audit', '--package-lock-only', '--json', ...extra], {
    cwd: frontend,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (!result.stdout) {
    throw new Error(`npm audit produced no output: ${result.error?.message ?? result.stderr}`);
  }
  try {
    return JSON.parse(result.stdout);
  } catch {
    throw new Error(`npm audit did not return JSON: ${result.stdout.slice(0, 200)}`);
  }
}

function ghsaFromUrl(url) {
  const match = /GHSA-[a-z0-9-]+/i.exec(url ?? '');
  return match ? match[0].toUpperCase() : null;
}

/** Unique advisories reported anywhere in an npm audit JSON document. */
export function collectAdvisories(report) {
  const advisories = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vulnerability.via ?? []) {
      if (!via || typeof via !== 'object' || !via.url) continue;
      const advisory = ghsaFromUrl(via.url);
      if (!advisory) continue;
      const pkg = via.name ?? vulnerability.name;
      const key = `${advisory}:${pkg}`;
      advisories.set(key, {
        advisory,
        package: pkg,
        severity: via.severity ?? vulnerability.severity ?? 'unknown',
        title: via.title ?? '',
        range: via.range ?? '',
      });
    }
  }
  return [...advisories.values()];
}

/** Every package npm considers affected by a given advisory, including dependents. */
export function affectedPackages(report, rootPackage) {
  const seen = new Set([rootPackage]);
  const queue = [rootPackage];
  while (queue.length) {
    const name = queue.shift();
    for (const dependent of report.vulnerabilities?.[name]?.effects ?? []) {
      if (!seen.has(dependent)) {
        seen.add(dependent);
        queue.push(dependent);
      }
    }
  }
  return [...seen].sort();
}

function productionFindingCount(report) {
  return Object.keys(report.vulnerabilities ?? {}).length;
}

/**
 * Pure decision function used by the CLI and unit tests.
 * `today` is an ISO date (UTC) so expiry is deterministic in tests.
 */
export function assessAudit({
  report,
  production,
  exceptions,
  lockPackages,
  today = new Date().toISOString().slice(0, 10),
}) {
  const problems = [];
  const accepted = [];
  const advisories = collectAdvisories(report);
  const configured = Array.isArray(exceptions?.exceptions) ? exceptions.exceptions : [];
  const used = new Set();

  if (!exceptions || exceptions.schema !== 1 || !Array.isArray(exceptions.exceptions)) {
    problems.push('security/dependency-audit-exceptions.json is not a schema-1 exception file.');
  }
  if (productionFindingCount(production) > 0) {
    problems.push(
      `Production-only audit reports ${productionFindingCount(production)} affected package(s); runtime findings are never accepted.`,
    );
  }

  for (const advisory of advisories) {
    const exception = configured.find(
      (entry) =>
        entry.advisory?.toUpperCase() === advisory.advisory && entry.package === advisory.package,
    );
    if (!exception) {
      problems.push(
        `Unaccepted advisory ${advisory.advisory} (${advisory.package}, ${advisory.severity}).`,
      );
      continue;
    }
    used.add(exception);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(exception.expires ?? '')) {
      problems.push(`Exception ${advisory.advisory} has no valid ISO expiry date.`);
      continue;
    }
    if (exception.expires < today) {
      problems.push(
        `Exception ${advisory.advisory} expired on ${exception.expires}; re-review it or upgrade the dependency.`,
      );
      continue;
    }
    if (exception.scope !== 'development') {
      problems.push(`Exception ${advisory.advisory} must be scoped to development.`);
      continue;
    }
    const affected = affectedPackages(report, advisory.package);
    const entriesFor = (name) =>
      Object.entries(lockPackages ?? {}).filter(
        ([key]) => key === `node_modules/${name}` || key.endsWith(`/node_modules/${name}`),
      );
    const runtime = affected.filter((name) => {
      const entries = entriesFor(name);
      return entries.length === 0 || entries.some(([, entry]) => entry.dev !== true);
    });
    if (runtime.length) {
      problems.push(
        `Exception ${advisory.advisory} reaches non-development packages: ${runtime.join(', ')}.`,
      );
      continue;
    }
    const approvedBy = exception.approvedBy ?? exceptions.approvedBy;
    const approvedAt = exception.approvedAt ?? exceptions.approvedAt;
    const reviewReference = exception.reviewReference ?? exceptions.reviewReference;
    if (!exception.rationale || !approvedBy || !approvedAt) {
      problems.push(`Exception ${advisory.advisory} needs rationale, approver and approval date.`);
      continue;
    }
    accepted.push({
      ...advisory,
      affected,
      exception: { ...exception, approvedBy, approvedAt, reviewReference },
    });
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
    advisories,
    productionClean: productionFindingCount(production) === 0,
  };
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
      (entry) =>
        entry.advisory?.toUpperCase() === advisory.advisory && entry.package === advisory.package,
    );
    console.log(
      `  ${accepted ? 'ACCEPTED' : 'UNACCEPTED'} ${advisory.advisory} ` +
        `${advisory.package} (${advisory.severity}, ${advisory.range}): ${advisory.title}`,
    );
  }
  console.log(`Production-only audit: ${verdict.productionClean ? 'clean' : 'FINDINGS'}.`);

  for (const entry of verdict.accepted) {
    console.error(
      `RISK ACCEPTANCE ACTIVE: ${entry.advisory} (${entry.package}, ${entry.severity}).\n` +
        `  packages: ${entry.affected.join(', ')}\n` +
        `  scope: development only\n` +
        `  approved by: ${entry.exception.approvedBy} on ${entry.exception.approvedAt}\n` +
        `  review: ${entry.exception.reviewReference ?? 'n/a'}\n` +
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
  console.log('Dependency audit accepted the configured exception(s); all other checks passed.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(`AUDIT FAILURE: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
