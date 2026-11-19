import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  APPEARANCE_INIT,
  collectInitialGraph,
  compareInitialGraph,
  normalizeAssetPath,
} from './bundleGraph.mjs';
const checker = fileURLToPath(new URL('./check-bundle-budget.mjs', import.meta.url));
async function fixture(manifest, files, run) {
  const root = await mkdtemp(join(tmpdir(), 'hnefatafl-bundle-budget-'));
  try {
    manifest.authenticated = {
      name: 'AuthenticatedArena',
      file: 'assets/authenticated.js',
      imports: ['index.html'],
    };
    await mkdir(join(root, '.vite'));
    await mkdir(join(root, 'assets'));
    await writeFile(join(root, '.vite', 'manifest.json'), JSON.stringify(manifest));
    await writeFile(join(root, 'assets/appearance-init.js'), '/* first paint */');
    await writeFile(join(root, 'assets/authenticated.js'), 'auth');
    for (const [name, contents] of Object.entries(files))
      await writeFile(join(root, name), contents);
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}
const check = (root) =>
  spawnSync(process.execPath, [checker, `--dist=${root}`], { encoding: 'utf8', timeout: 10000 });
test('counts transitive eager chunks once, includes first paint, and excludes optional dynamic chunks', async () => {
  await fixture(
    {
      'index.html': {
        file: 'assets/main.js',
        imports: ['shared'],
        dynamicImports: ['training'],
        css: ['assets/main.css'],
      },
      shared: { file: 'assets/shared.js', imports: ['shared'] },
      training: { file: 'assets/training.js' },
    },
    {
      'assets/main.js': 'a',
      'assets/shared.js': 'bb',
      'assets/main.css': 'body{}',
      'assets/training.js': 'x'.repeat(700000),
    },
    async (root) => {
      const result = check(root);
      assert.equal(result.status, 0, result.stderr);
      const measured = JSON.parse(result.stdout);
      assert.deepEqual(measured.javascript.files, [
        'assets/appearance-init.js',
        'assets/authenticated.js',
        'assets/main.js',
        'assets/shared.js',
      ]);
      assert.equal(measured.javascript.raw, '/* first paint */'.length + 7);
      assert.equal(measured.css.raw, 6);
    },
  );
});
test('counts the route-selected authenticated graph even when it is a dynamic import', async () => {
  await fixture(
    { 'index.html': { file: 'assets/main.js', dynamicImports: ['authenticated'] } },
    { 'assets/main.js': 'small' },
    async (root) => {
      await writeFile(join(root, 'assets/authenticated.js'), 'x'.repeat(600000));
      const result = check(root);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /exceeds its measured budget/);
    },
  );
});
test('rejects a missing authenticated route instead of measuring only the tiny router', async () => {
  await fixture(
    { 'index.html': { file: 'assets/main.js' } },
    { 'assets/main.js': 'small' },
    async (root) => {
      await writeFile(
        join(root, '.vite/manifest.json'),
        JSON.stringify({ 'index.html': { file: 'assets/main.js' } }),
      );
      const result = check(root);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /AuthenticatedArena entry/);
    },
  );
});
test('rejects an oversized transitive eager graph even when the entry itself is small', async () => {
  await fixture(
    {
      'index.html': { file: 'assets/main.js', imports: ['shared'] },
      shared: { file: 'assets/shared.js' },
    },
    { 'assets/main.js': 'small', 'assets/shared.js': 'x'.repeat(600000) },
    async (root) => {
      const result = check(root);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /exceeds its measured budget/);
    },
  );
});
test('refuses incomplete manifests instead of silently undercounting missing dependencies', async () => {
  await fixture(
    { 'index.html': { file: 'assets/main.js', imports: ['missing'] } },
    { 'assets/main.js': 'small' },
    async (root) => {
      const result = check(root);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /Missing manifest entry/);
    },
  );
});
test('refuses missing first-paint assets instead of passing an incomplete production build', async () => {
  await fixture(
    { 'index.html': { file: 'assets/main.js' } },
    { 'assets/main.js': 'small' },
    async (root) => {
      await rm(join(root, 'assets/appearance-init.js'));
      const result = check(root);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /appearance-init/);
    },
  );
});

test('collectInitialGraph deduplicates shared eager dependencies and requires the authenticated entry', () => {
  const manifest = {
    'index.html': { file: 'assets/main.js', imports: ['shared'], css: ['assets/main.css'] },
    shared: { file: 'assets/shared.js', imports: ['shared', 'leaf'] },
    leaf: { file: 'assets/leaf.js' },
    AuthenticatedArena: { name: 'AuthenticatedArena', file: 'assets/authenticated.js' },
  };
  const graph = collectInitialGraph(manifest);
  assert.deepEqual([...graph.scripts].sort(), [
    APPEARANCE_INIT,
    'assets/authenticated.js',
    'assets/leaf.js',
    'assets/main.js',
    'assets/shared.js',
  ]);
  assert.deepEqual([...graph.styles], ['assets/main.css']);
  assert.throws(
    () => collectInitialGraph({ 'index.html': { file: 'assets/main.js' } }),
    /AuthenticatedArena entry/,
  );
  assert.throws(
    () => collectInitialGraph({ 'index.html': { file: 'assets/main.js', imports: ['gone'] } }),
    /Missing manifest entry: gone/,
  );
});

test('compareInitialGraph reports unexpected delayed loads and missing initial assets', () => {
  const expected = ['assets/appearance-init.js', 'assets/main.js', 'assets/shared.js'];
  const observed = ['/assets/main.js', '/assets/shared.js', '/assets/Training-abc123.js'];
  assert.deepEqual(compareInitialGraph(expected, observed), {
    unexpected: ['assets/Training-abc123.js'],
    missing: ['assets/appearance-init.js'],
  });
  assert.deepEqual(
    compareInitialGraph(expected, [
      'assets/main.js',
      'assets/shared.js',
      'assets/appearance-init.js',
    ]),
    {
      unexpected: [],
      missing: [],
    },
  );
  assert.equal(normalizeAssetPath('http://127.0.0.1:1/assets/main.js?t=1'), 'assets/main.js');
  assert.equal(normalizeAssetPath('/assets/main.css'), 'assets/main.css');
});
