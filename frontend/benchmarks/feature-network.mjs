import { chromium } from 'playwright';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  fingerprintProductionBundle,
  readGitRevision,
  verifyBuildRevision,
  verifyBuildFingerprint,
} from './buildRevision.mjs';
import { createServer } from 'node:http';
import { readFile, stat, writeFile } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';
import { gzipSync } from 'node:zlib';
import {
  collectInitialGraph,
  collectRouteGraph,
  compareInitialGraph,
  normalizeAssetPath,
  routeChunk,
} from '../scripts/bundleGraph.mjs';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const root = resolve(frontend, 'dist');
const output = resolve(process.argv[2] || '/tmp/hnefatafl-feature-network.json');
const source = readGitRevision(repository);
const allowDirty = process.argv.includes('--allow-dirty');
const build = JSON.parse(await readFile(join(root, 'arena-build.json'), 'utf8'));
const checked = verifyBuildRevision(build, source, { allowDirty });
if (!checked.ok) throw new Error(checked.message);
const fingerprint = verifyBuildFingerprint(build, await fingerprintProductionBundle(root));
if (!fingerprint.ok) throw new Error(fingerprint.message);

const manifest = JSON.parse(await readFile(join(root, '.vite', 'manifest.json'), 'utf8'));
const expected = collectInitialGraph(manifest);
const publicGraph = collectRouteGraph(manifest, 'PublicArena');
const authenticatedEntry = routeChunk(manifest, 'AuthenticatedArena')[1].file;
const publicEntry = routeChunk(manifest, 'PublicArena')[1].file;
// Public and authenticated route entries must not pull each other in. Runtime
// isolation is additionally covered by the public Watch Hall production suite.
if (expected.scripts.has(publicEntry))
  throw new Error('Authenticated initial graph includes the public entry');
if (publicGraph.scripts.has(authenticatedEntry))
  throw new Error('Public entry graph includes the authenticated application');

const types = {
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.html': 'text/html',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
};
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    let path = resolve(root, '.' + pathname);
    if (path !== root && !path.startsWith(root + sep)) throw Error('outside');
    let file;
    try {
      file = await stat(path);
    } catch {
      // Real assets must 404; only unknown document routes fall back to the SPA.
      if (pathname.startsWith('/assets/')) {
        res.writeHead(404).end();
        return;
      }
      path = resolve(root, 'index.html');
      file = await stat(path);
    }
    if (file.isDirectory()) path = resolve(path, 'index.html');
    res.setHeader('Content-Type', types[extname(path)] || 'application/octet-stream');
    res.end(await readFile(path));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;

const account = {
  id: 'a'.repeat(32),
  username: 'BundleFixture',
  role: 'ADMIN',
  active: 1,
  permissions: ['play', 'profile', 'training', 'tournaments', 'models', 'accounts', 'all_data'],
  email_verified: true,
};
const profile = {
  username: account.username,
  email: 'fixture@example.test',
  email_verified: true,
  display_name: null,
  country_code: null,
  city: null,
  date_of_birth: null,
  legacy_birth_year: null,
  legacy_private_fields: [],
  country_codes: [],
  portrait_id: 'shieldbearer',
};
const leaderboard = {
  ruleset: 'copenhagen',
  entries: [],
  total: 0,
  limit: 5,
  offset: 0,
  me: null,
  policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
};

/** Read-only synthetic API fixtures shared by every page in the probe. */
async function mockApi(page) {
  const unexpectedMutations = [];
  await page.route('**/api/**', (route) => {
    if (route.request().method() !== 'GET') {
      unexpectedMutations.push(route.request().method() + ' ' + route.request().url());
      return route.abort();
    }
    const p = new URL(route.request().url()).pathname;
    const json =
      p === '/api/auth/me'
        ? { account, csrf_token: 'fixture-only' }
        : p === '/api/profile'
          ? profile
          : p === '/api/portraits/catalog'
            ? {
                default_portrait_id: 'shieldbearer',
                items: [{ id: 'shieldbearer', name: 'Shieldbearer', unlock_achievement_id: null }],
              }
            : p === '/api/personal/achievements'
              ? { items: [], summary: { unlocked: 0, total: 0 } }
              : p === '/api/personal/progress'
                ? { streaks: { by_ruleset: [] } }
                : p === '/api/leaderboard'
                  ? leaderboard
                  : p === '/api/history/games'
                    ? { games: [], totals: [], matching: 0, limit: 3, next_cursor: null }
                    : p.startsWith('/api/personal/')
                      ? {
                          games: [],
                          totals: { total_games: 0, in_progress: 0 },
                          overview: { your_turn: 0, other_active: 0, waiting: 0 },
                          statistics: { groups: [] },
                        }
                      : p === '/api/training/device'
                        ? {
                            device: 'cpu',
                            cuda_devices: [],
                            cuda_available: false,
                            training_devices: [],
                          }
                        : p === '/api/online/availability'
                          ? { enabled: false, eligible: false }
                          : [];
    return route.fulfill({ json });
  });
  return unexpectedMutations;
}

/** Track every asset response with its status and content type. */
function observeAssets(page) {
  const responses = new Map();
  page.on('response', (response) => {
    const asset = normalizeAssetPath(response.url());
    if (!asset.startsWith('assets/')) return;
    if (!asset.endsWith('.js') && !asset.endsWith('.css')) return;
    responses.set(asset, {
      status: response.status(),
      contentType: response.headers()['content-type'] ?? '',
    });
  });
  return responses;
}

const assets = (responses, extension) =>
  [...responses.keys()].filter((asset) => asset.endsWith(extension)).sort();

function verifyAssetResponses(responses, files, kind) {
  const failures = [];
  for (const file of files) {
    const observed = responses.get(file);
    if (!observed) {
      failures.push(`${file}: no response observed`);
      continue;
    }
    if (observed.status !== 200) failures.push(`${file}: HTTP ${observed.status}`);
    const expectedType = kind === 'js' ? 'javascript' : 'text/css';
    if (!observed.contentType.includes(expectedType))
      failures.push(`${file}: content-type ${observed.contentType || 'missing'}`);
  }
  if (failures.length)
    throw new Error(`Invalid initial ${kind} responses:\n${failures.join('\n')}`);
}

async function inventory(files) {
  const rows = [];
  for (const file of files) {
    const bytes = await readFile(join(root, file));
    rows.push({ file, raw: bytes.length, gzip_level9: gzipSync(bytes, { level: 9 }).length });
  }
  return rows;
}

let browser;
try {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  const unexpectedMutations = await mockApi(page);
  const responses = observeAssets(page);

  await page.goto(origin + '/dashboard');
  await page.locator('.personal-dashboard').waitFor();
  // Let Dashboard initialization settle so delayed eager/feature requests are
  // observed rather than missed immediately after the first paint.
  await page.waitForLoadState('networkidle');

  const initialFiles = assets(responses, '.js');
  const initialCssFiles = assets(responses, '.css');
  const jsComparison = compareInitialGraph(expected.scripts, initialFiles);
  const cssComparison = compareInitialGraph(expected.styles, initialCssFiles);
  if (jsComparison.unexpected.length)
    throw new Error('Unexpected initial JavaScript: ' + JSON.stringify(jsComparison.unexpected));
  if (jsComparison.missing.length)
    throw new Error('Missing initial JavaScript: ' + JSON.stringify(jsComparison.missing));
  if (cssComparison.unexpected.length)
    throw new Error('Unexpected initial CSS: ' + JSON.stringify(cssComparison.unexpected));
  if (cssComparison.missing.length)
    throw new Error('Missing initial CSS: ' + JSON.stringify(cssComparison.missing));
  verifyAssetResponses(responses, initialFiles, 'js');
  verifyAssetResponses(responses, initialCssFiles, 'css');

  const initialInventory = await inventory(initialFiles);
  const initialCssInventory = await inventory(initialCssFiles);

  const forbidden = [
    'Training',
    'Tournaments',
    'RecordedReview',
    'AgentAnalysis',
    'AdvisorSelect',
    'NewGameForm',
    'StatisticsPage',
    'ProfilePage',
    'Online',
  ];
  const unexpectedlyEager = forbidden.filter((name) =>
    initialFiles.some((file) => file.includes(`/${name}-`)),
  );
  if (unexpectedlyEager.length)
    throw new Error('Unexpected eager feature ' + JSON.stringify(unexpectedlyEager));

  // Workshop navigation must request its own deferred chunk after first use.
  await page.getByRole('link', { name: 'AI Workshop', exact: true }).click();
  await page.locator('.training-workshop').waitFor();
  const afterTraining = assets(responses, '.js');
  if (!afterTraining.some((file) => file.includes('/Training-')))
    throw new Error('Training chunk not loaded after Workshop navigation');
  if (afterTraining.some((file) => file.includes('/Tournaments-')))
    throw new Error(
      'Training navigation must not eagerly load Tournaments: ' + JSON.stringify(afterTraining),
    );

  // Production recovery: hold the real hashed New Game form chunk (including
  // Vite's preload attempt) until the modal reports the failure, then recover
  // through Retry on the same page.
  const formChunk = Object.values(manifest).find((chunk) => chunk.name === 'NewGameForm')?.file;
  if (!formChunk) throw new Error('NewGameForm chunk missing from the production manifest');
  const recoveryPage = await context.newPage();
  await recoveryPage.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  const recoveryMutations = await mockApi(recoveryPage);
  const recoveryResponses = observeAssets(recoveryPage);
  const formName = formChunk.split('/').pop();
  const formPattern = (url) => url.pathname.endsWith(`/${formName}`);
  let blockForm = true;
  await recoveryPage.route(formPattern, (route) => (blockForm ? route.abort() : route.continue()));
  await recoveryPage.goto(origin + '/dashboard');
  await recoveryPage.locator('.personal-dashboard').waitFor();
  await recoveryPage.getByRole('button', { name: 'Play vs AI', exact: true }).click();
  const recoveryDialog = recoveryPage.getByRole('dialog', {
    name: 'Begin a new saga',
    exact: true,
  });
  await recoveryDialog.getByRole('alert').waitFor();
  const retry = recoveryDialog.getByRole('button', { name: 'Retry', exact: true });
  await retry.waitFor();
  // Vite preloads the module and its dependencies before importing it, so a
  // single aborted request can fall back to the import. Hold the failure until
  // the modal reports it, then prove the explicit Retry recovers the module.
  blockForm = false;
  await retry.click();
  await recoveryDialog.getByRole('button', { name: 'To the table' }).waitFor();
  if (!assets(recoveryResponses, '.js').includes(formChunk))
    throw new Error('New Game form chunk did not recover through Retry');
  if (recoveryMutations.length)
    throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(recoveryMutations));
  const recovery = {
    feature: formChunk,
    firstAttempt: 'aborted',
    recoveredThrough: 'Retry',
  };

  if (unexpectedMutations.length)
    throw new Error('Unexpected API mutations: ' + JSON.stringify(unexpectedMutations));

  const report = {
    sourceHead: source.revision,
    build,
    measuredAt: new Date().toISOString(),
    browser: browser.version(),
    node: process.version,
    fixture:
      'Production static assets; read-only mocked account/dashboard/workshop API responses. Network graph proof, not backend capacity evidence.',
    manifest: {
      authenticatedEntry,
      publicEntry,
      expectedInitialScripts: [...expected.scripts].sort(),
      expectedInitialStyles: [...expected.styles].sort(),
    },
    initialFiles,
    initialCssFiles,
    initialInventory,
    initialCssInventory,
    initialRaw: initialInventory.reduce((n, f) => n + f.raw, 0),
    initialGzipLevel9: initialInventory.reduce((n, f) => n + f.gzip_level9, 0),
    initialCssRaw: initialCssInventory.reduce((n, f) => n + f.raw, 0),
    initialCssGzipLevel9: initialCssInventory.reduce((n, f) => n + f.gzip_level9, 0),
    observedResponses: Object.fromEntries([...responses].sort()),
    deferredFeaturesAbsentInitially: forbidden,
    afterTrainingFiles: afterTraining,
    recovery,
    publicEntryIsolation: {
      authenticatedEntry,
      publicEntry,
      authenticatedGraphIncludesPublicEntry: expected.scripts.has(publicEntry),
      publicGraphIncludesAuthenticatedEntry: publicGraph.scripts.has(authenticatedEntry),
    },
  };
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
