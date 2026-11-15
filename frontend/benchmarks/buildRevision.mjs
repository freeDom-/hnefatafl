/** Reproducible build-revision and content-fingerprint helpers for benchmarks. */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Read the current git revision and whether the working tree has uncommitted
 * changes. `run` is injectable so the verification can be tested without git.
 * A source archive without `.git` yields `git: false` instead of breaking the
 * production build; the benchmark rejects it because it cannot be verified.
 */
export function readGitRevision(repository, run = execFileSync) {
  const options = {
    cwd: repository,
    encoding: 'utf8',
    timeout: 5000,
    stdio: ['ignore', 'pipe', 'ignore'],
  };
  try {
    const revision = run('git', ['rev-parse', 'HEAD'], options).trim();
    const dirty = run('git', ['status', '--porcelain'], options).trim().length > 0;
    return { revision, dirty, git: true };
  } catch {
    return { revision: 'unknown', dirty: true, git: false };
  }
}

async function walk(directory, prefix = '') {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const full = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full, relative)));
    else if (entry.isFile()) files.push({ relative, full });
  }
  return files;
}

/** Deterministic SHA-256 over every file path and byte in a directory tree. */
export async function fingerprintDirectory(directory) {
  const hash = createHash('sha256');
  for (const { relative, full } of await walk(directory)) {
    hash.update(relative);
    hash.update('\0');
    hash.update(await readFile(full));
    hash.update('\0');
  }
  return hash.digest('hex');
}

/**
 * Compare the revision recorded by the production build with the checkout under
 * test. Returns a structured result so callers can surface an actionable error
 * instead of silently benchmarking a stale or non-reproducible bundle.
 */
export function verifyBuildRevision(build, current, { allowDirty = false } = {}) {
  if (!build || typeof build.revision !== 'string' || !build.revision)
    return {
      ok: false,
      reason: 'missing',
      message:
        'No build revision recorded in dist/arena-build.json. Rebuild with `npm --prefix frontend run build`.',
    };
  if (current.git === false || build.git === false || build.revision === 'unknown')
    return {
      ok: false,
      reason: 'no-git',
      message:
        'Build revision cannot be verified because this is not a git checkout. Run the benchmark from a git clone of the repository.',
    };
  if (build.revision !== current.revision)
    return {
      ok: false,
      reason: 'stale',
      message: `Stale production build: dist was built from ${build.revision} but HEAD is ${current.revision}. Rebuild with \`npm --prefix frontend run build\`.`,
    };
  if ((build.dirty || current.dirty) && !allowDirty)
    return {
      ok: false,
      reason: 'dirty',
      message: `Build revision ${build.revision} has uncommitted changes (build dirty: ${build.dirty}, source dirty: ${current.dirty}). Commit or stash them, or pass --allow-dirty to record a non-reproducible local measurement.`,
    };
  return { ok: true, reason: 'match', message: '' };
}
