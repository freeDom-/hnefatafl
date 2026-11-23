import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import {
  generateThirdPartyLicenses,
  resolveInstalledPath,
  resolveRuntimePackages,
  writeLicenseArtifacts,
} from './licenseArtifacts.mjs';

function packageLock(entries) {
  return { lockfileVersion: 3, packages: { '': { dependencies: {} }, ...entries } };
}

async function writePackage(root, path, metadata, license = 'MIT License\n\nShared license text.') {
  const directory = join(root, path);
  await mkdir(directory, { recursive: true });
  if (metadata !== null) {
    await writeFile(join(directory, 'package.json'), JSON.stringify(metadata));
  }
  if (license !== null) await writeFile(join(directory, 'LICENSE'), license);
}

test('production closure keeps nested versions distinct and separates type-only and dev packages', () => {
  const lock = packageLock({
    '': { dependencies: { a: '1', b: '1', '@types/example': '1', 'dev-tool': '1' } },
    'node_modules/a': { version: '1.0.0', license: 'MIT', dependencies: { b: '^2' } },
    'node_modules/a/node_modules/b': { version: '2.0.0', license: 'MIT' },
    'node_modules/b': { version: '1.0.0', license: 'MIT' },
    'node_modules/@types/example': { version: '1.0.0', license: 'MIT' },
    'node_modules/dev-tool': { version: '1.0.0', license: 'MIT', dev: true },
  });
  const closure = resolveRuntimePackages(lock);
  assert.deepEqual(
    closure.runtime.map((entry) => entry.path),
    ['node_modules/a', 'node_modules/a/node_modules/b', 'node_modules/b'],
  );
  assert.deepEqual(
    closure.typeOnly.map((entry) => entry.path),
    ['node_modules/@types/example'],
  );
  assert.equal(closure.developmentCount, 1);
  assert.equal(
    resolveInstalledPath(lock.packages, 'node_modules/a', 'b'),
    'node_modules/a/node_modules/b',
  );
  assert.equal(
    resolveInstalledPath(lock.packages, 'node_modules/a/node_modules/b', 'missing'),
    null,
  );
});

test('required dependencies must resolve and optional missing packages are reported but allowed', () => {
  assert.throws(
    () => resolveRuntimePackages(packageLock({ '': { dependencies: { missing: '1' } } })),
    /Missing lockfile package for required dependency/,
  );
  const lock = packageLock({
    '': { optionalDependencies: { platform: '1' } },
    'node_modules/root': {
      version: '1.0.0',
      license: 'MIT',
      optionalDependencies: { platform: '1' },
    },
  });
  lock.packages[''].dependencies = { root: '1' };
  const closure = resolveRuntimePackages(lock, {
    isInstalled: (path) => path !== 'node_modules/platform',
  });
  assert.deepEqual(closure.missingOptional, ['platform']);
});

test('incomplete package metadata and missing license files fail generation loudly', async () => {
  const top = await mkdtemp(join(tmpdir(), 'hnefatafl-license-test-'));
  const frontend = join(top, 'frontend');
  const packages = {
    '': { dependencies: { invalid: '1', 'missing-license': '1' } },
    'node_modules/invalid': { version: '1.0.0', license: 'MIT' },
    'node_modules/missing-license': { version: '1.0.0', license: 'MIT' },
  };
  try {
    await mkdir(frontend, { recursive: true });
    await writeFile(join(frontend, 'package-lock.json'), JSON.stringify({ packages }));
    await mkdir(join(frontend, 'node_modules/invalid'), { recursive: true });
    await writeFile(join(frontend, 'node_modules/invalid/package.json'), '{invalid json');
    await writePackage(
      frontend,
      'node_modules/missing-license',
      {
        name: 'missing-license',
        version: '1.0.0',
        license: 'MIT',
      },
      null,
    );
    await assert.rejects(
      () => generateThirdPartyLicenses({ frontendDirectory: frontend }),
      (error) =>
        /incomplete third-party license inventory/i.test(error.message) &&
        /node_modules\/invalid/.test(error.message) &&
        /node_modules\/missing-license/.test(error.message),
    );
  } finally {
    await rm(top, { recursive: true, force: true });
  }
});

test('generation is deterministic, sorted and does not repeat identical license texts', async () => {
  const top = await mkdtemp(join(tmpdir(), 'hnefatafl-license-test-'));
  const frontend = join(top, 'frontend');
  const repository = join(top, 'repository');
  const output = join(top, 'output');
  const lock = packageLock({
    '': { dependencies: { zed: '1', alpha: '1' } },
    'node_modules/zed': { version: '2.0.0', license: 'MIT' },
    'node_modules/alpha': { version: '1.0.0', license: 'MIT' },
  });
  try {
    await mkdir(frontend, { recursive: true });
    await mkdir(repository, { recursive: true });
    await writeFile(join(frontend, 'package-lock.json'), JSON.stringify(lock));
    await writePackage(frontend, 'node_modules/zed', {
      name: 'zed',
      version: '2.0.0',
      license: 'MIT',
    });
    await writePackage(frontend, 'node_modules/alpha', {
      name: 'alpha',
      version: '1.0.0',
      license: 'MIT',
    });
    await writeFile(join(repository, 'LICENSE'), 'project license\n');
    await writeFile(join(repository, 'THIRD_PARTY_NOTICES.md'), 'notices\n');

    await writeLicenseArtifacts({
      frontendDirectory: frontend,
      repositoryDirectory: repository,
      outputDirectory: output,
    });
    const first = await readFile(join(output, 'THIRD_PARTY_LICENSES.txt'), 'utf8');
    await writeLicenseArtifacts({
      frontendDirectory: frontend,
      repositoryDirectory: repository,
      outputDirectory: output,
    });
    const second = await readFile(join(output, 'THIRD_PARTY_LICENSES.txt'), 'utf8');

    assert.equal(first, second);
    assert.ok(first.indexOf('===== alpha@1.0.0 =====') < first.indexOf('===== zed@2.0.0 ====='));
    assert.equal((first.match(/Shared license text\./g) ?? []).length, 1);
    assert.match(first, /Runtime packages: 2/);
    assert.match(first, /Identical text already included for alpha@1\.0\.0/);
    assert.equal(await readFile(join(output, 'LICENSE.txt'), 'utf8'), 'project license\n');
  } finally {
    await rm(top, { recursive: true, force: true });
  }
});

test('the repository runtime inventory is complete and deterministic', async () => {
  const first = await generateThirdPartyLicenses();
  const second = await generateThirdPartyLicenses();
  assert.equal(first.text, second.text);
  assert.ok(first.counts.runtime > 0);
  assert.match(first.text, /Development-only packages skipped: \d+/);
  assert.match(first.text, /Type-only packages not distributed: \d+/);
});
