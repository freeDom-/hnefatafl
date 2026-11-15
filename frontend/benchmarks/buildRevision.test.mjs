import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fingerprintDirectory, readGitRevision, verifyBuildRevision } from './buildRevision.mjs';

const current = { revision: 'a'.repeat(40), dirty: false, git: true };

test('accepts a clean build recorded at the current revision', () => {
  const result = verifyBuildRevision({ revision: current.revision, dirty: false }, current);
  assert.equal(result.ok, true);
  assert.equal(result.reason, 'match');
});

test('rejects a missing fingerprint with rebuild instructions', () => {
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
  const result = verifyBuildRevision({ revision: 'b'.repeat(40), dirty: false }, current);
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'stale');
  assert.match(result.message, /b{40}/);
  assert.match(result.message, /a{40}/);
  assert.match(result.message, /npm --prefix frontend run build/);
});

test('rejects a dirty build or dirty source unless explicitly allowed', () => {
  const dirtyBuild = verifyBuildRevision({ revision: current.revision, dirty: true }, current);
  assert.equal(dirtyBuild.ok, false);
  assert.equal(dirtyBuild.reason, 'dirty');
  const dirtySource = verifyBuildRevision(
    { revision: current.revision, dirty: false },
    {
      revision: current.revision,
      dirty: true,
    },
  );
  assert.equal(dirtySource.ok, false);
  assert.equal(dirtySource.reason, 'dirty');
  const allowed = verifyBuildRevision({ revision: current.revision, dirty: true }, current, {
    allowDirty: true,
  });
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
