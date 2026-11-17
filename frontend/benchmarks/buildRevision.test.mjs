import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import {
  fingerprintDirectory,
  fingerprintProductionBundle,
  readGitRevision,
  verifyBuildFingerprint,
  verifyBuildRevision,
} from './buildRevision.mjs';

const current = { revision: 'a'.repeat(40), dirty: false, git: true };

async function makeBundle() {
  const dist = await mkdtemp(join(tmpdir(), 'arena-build-bundle-'));
  await mkdir(join(dist, 'assets'), { recursive: true });
  await writeFile(join(dist, 'index.html'), '<!doctype html><div id="root"></div>\n');
  await writeFile(join(dist, 'assets', 'index-abc.js'), 'console.log("bundle");\n');
  await writeFile(join(dist, 'assets', 'index-abc.css'), 'body { color: black; }\n');
  return dist;
}

test('accepts a clean build recorded at the current revision', () => {
  const result = verifyBuildRevision(
    { revision: current.revision, dirty: false, git: true },
    current,
  );
  assert.equal(result.ok, true);
  assert.equal(result.reason, 'match');
});

test('rejects a missing build record or revision with rebuild instructions', () => {
  for (const build of [null, undefined, {}, { revision: '' }]) {
    const result = verifyBuildRevision(build, current);
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'missing');
    assert.match(result.message, /npm --prefix frontend run build/);
  }
});

test('rejects an unverifiable build from a non-git checkout', () => {
  const noGit = verifyBuildRevision(
    { revision: 'unknown', dirty: true, git: false },
    { revision: 'unknown', dirty: true, git: false },
  );
  assert.equal(noGit.ok, false);
  assert.equal(noGit.reason, 'no-git');
  assert.match(noGit.message, /git checkout/);
  const sourceNoGit = verifyBuildRevision(
    { revision: current.revision, dirty: false, git: true },
    { revision: 'unknown', dirty: true, git: false },
  );
  assert.equal(sourceNoGit.ok, false);
  assert.equal(sourceNoGit.reason, 'no-git');
});

test('rejects a stale build and names both revisions', () => {
  const result = verifyBuildRevision(
    { revision: 'b'.repeat(40), dirty: false, git: true },
    current,
  );
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'stale');
  assert.match(result.message, /b{40}/);
  assert.match(result.message, /a{40}/);
  assert.match(result.message, /npm --prefix frontend run build/);
});

test('rejects a dirty build or dirty source unless explicitly allowed', () => {
  const dirtyBuild = verifyBuildRevision(
    { revision: current.revision, dirty: true, git: true },
    current,
  );
  assert.equal(dirtyBuild.ok, false);
  assert.equal(dirtyBuild.reason, 'dirty');
  const dirtySource = verifyBuildRevision(
    { revision: current.revision, dirty: false, git: true },
    {
      revision: current.revision,
      dirty: true,
      git: true,
    },
  );
  assert.equal(dirtySource.ok, false);
  assert.equal(dirtySource.reason, 'dirty');
  const allowed = verifyBuildRevision(
    { revision: current.revision, dirty: true, git: true },
    current,
    {
      allowDirty: true,
    },
  );
  assert.equal(allowed.ok, true);
});

test('reads the revision and dirty state through the injected runner', () => {
  const calls = [];
  const run = (command, args) => {
    calls.push([command, ...args]);
    return args.includes('--porcelain') ? ' M frontend/src/App.tsx\n' : `${current.revision}\n`;
  };
  assert.deepEqual(readGitRevision('/repo', run), {
    revision: current.revision,
    dirty: true,
    git: true,
  });
  assert.equal(calls.length, 2);
});

test('falls back safely when git is unavailable', () => {
  const run = () => {
    throw new Error('not a git repository');
  };
  assert.deepEqual(readGitRevision('/archive', run), {
    revision: 'unknown',
    dirty: true,
    git: false,
  });
});

test('fingerprints directory contents deterministically and detects changes', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'arena-build-fingerprint-'));
  try {
    await mkdir(join(directory, 'nested'));
    await writeFile(join(directory, 'a.js'), 'alpha');
    await writeFile(join(directory, 'nested', 'b.css'), 'beta');
    const first = await fingerprintDirectory(directory);
    assert.match(first, /^[0-9a-f]{64}$/);
    assert.equal(await fingerprintDirectory(directory), first);
    await writeFile(join(directory, 'a.js'), 'changed');
    assert.notEqual(await fingerprintDirectory(directory), first);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('accepts a valid unmodified production build and identifies its bundle', async () => {
  const dist = await makeBundle();
  try {
    const fingerprint = await fingerprintProductionBundle(dist);
    assert.match(fingerprint, /^[0-9a-f]{64}$/);
    const record = { revision: current.revision, dirty: false, git: true, fingerprint };
    await writeFile(join(dist, 'arena-build.json'), JSON.stringify(record) + '\n');
    // The recorded metadata must not change the fingerprint of the bundle itself.
    const recomputed = await fingerprintProductionBundle(dist);
    assert.equal(recomputed, fingerprint);
    assert.equal(verifyBuildRevision(record, current).ok, true);
    assert.equal(verifyBuildFingerprint(record, recomputed).ok, true);
  } finally {
    await rm(dist, { recursive: true, force: true });
  }
});

test('excludes arena-build.json from the production bundle fingerprint', async () => {
  const dist = await makeBundle();
  try {
    const first = await fingerprintProductionBundle(dist);
    await writeFile(join(dist, 'arena-build.json'), JSON.stringify({ revision: 'x' }) + '\n');
    assert.equal(await fingerprintProductionBundle(dist), first);
    await writeFile(join(dist, 'arena-build.json'), JSON.stringify({ revision: 'changed' }) + '\n');
    assert.equal(await fingerprintProductionBundle(dist), first);
  } finally {
    await rm(dist, { recursive: true, force: true });
  }
});

test('requires a well-formed SHA-256 fingerprint', () => {
  const fingerprint = 'a'.repeat(64);
  for (const build of [
    null,
    undefined,
    {},
    { fingerprint: '' },
    { fingerprint: 'deadbeef' },
    { fingerprint: 'A'.repeat(64) },
    { fingerprint: 'a'.repeat(63) },
    { fingerprint: 'a'.repeat(65) },
  ]) {
    const result = verifyBuildFingerprint(build, fingerprint);
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'missing-fingerprint');
    assert.match(result.message, /npm --prefix frontend run build/);
  }
  const badActual = verifyBuildFingerprint({ fingerprint }, 'not-a-fingerprint');
  assert.equal(badActual.ok, false);
  assert.equal(badActual.reason, 'missing-fingerprint');
});

test('detects modified HTML, JavaScript and CSS in the production bundle', async () => {
  const dist = await makeBundle();
  try {
    const recorded = await fingerprintProductionBundle(dist);
    for (const file of ['index.html', 'assets/index-abc.js', 'assets/index-abc.css']) {
      const original = await fingerprintProductionBundle(dist);
      await writeFile(join(dist, file), '/* modified */\n');
      const modified = await fingerprintProductionBundle(dist);
      assert.notEqual(modified, original, `${file} must change the bundle fingerprint`);
      const result = verifyBuildFingerprint({ fingerprint: recorded }, modified);
      assert.equal(result.ok, false);
      assert.equal(result.reason, 'mismatch');
      assert.match(result.message, /Rebuild/);
      await writeFile(join(dist, file), 'restored\n');
    }
  } finally {
    await rm(dist, { recursive: true, force: true });
  }
});
