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
  compareAssetOutcomes,
  compareInitialGraph,
  findForbiddenModules,
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
// Build-only ownership map, written outside `dist` by the Vite plugin. It maps
// each emitted chunk to its source modules so a prohibited feature folded into
// an eager shared chunk is detected even without its filename prefix.
const ownership = JSON.parse(
  await readFile(join(frontend, '.cache', 'bundle-modules.json'), 'utf8'),
);
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
// The ownership map must describe every eager JavaScript chunk (the inline
// first-paint script is an asset, not a chunk) so a missing entry cannot hide a
// folded feature. Conversely every ownership entry must exist in `dist`.
for (const file of expected.scripts) {
  if (file === 'assets/appearance-init.js') continue;
  if (!ownership.chunks[file])
    throw new Error(`Missing module ownership metadata for eager chunk ${file}`);
}
for (const file of Object.keys(ownership.chunks)) {
  const match = Object.values(manifest).find((chunk) => chunk.file === file);
  if (!match) throw new Error(`Module ownership metadata references unknown chunk ${file}`);
}

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
// A minimal completed single-player review used to load the shared analysis
// stylesheet through RecordedReview, AgentAnalysis and AdvisorSelect.
const reviewBoard = Array(49).fill('.');
reviewBoard[24] = 'K';
for (const square of [0, 1, 5, 6, 7, 13, 35, 41, 42, 43, 47, 48]) reviewBoard[square] = 'A';
for (const square of [14, 15, 16, 17, 21, 22, 26, 27, 31, 32, 33, 34]) reviewBoard[square] = 'D';
const recordedReview = {
  game: {
    id: 'review-1',
    category: 'account-single-player',
    ruleset: 'brandubh',
    mode: 'human-ai',
    status: 'completed',
    winner: 'draw',
    plies: 1,
    players: {
      attackers: { name: 'BundleFixture', kind: 'human' },
      defenders: { name: 'Jarl', kind: 'ai' },
    },
  },
  game_id: 'review-1',
  version: 1,
  validated: true,
  cursor: 1,
  states: [
    { board: reviewBoard, ply: 0, turn: 'attackers', winner: null, reason: null },
    { board: reviewBoard, ply: 1, turn: 'defenders', winner: null, reason: null },
  ],
  moves: [{ source: 0, target: 1 }],
};
// A live single-player game whose analytics advisor makes the game page render
// both AgentAnalysis and AdvisorSelect concurrently.
const gameBoard = Array(81).fill('.');
gameBoard[40] = 'K';
for (const square of [
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 17, 18, 26, 27, 35, 36, 44, 45, 53, 54, 62, 63, 71, 72, 73, 74, 75,
  76, 77, 78, 79, 80,
])
  gameBoard[square] = 'A';
for (const square of [30, 31, 32, 38, 39, 41, 42, 48, 49, 50]) gameBoard[square] = 'D';
const gameFixture = {
  id: 'g1',
  ruleset: 'tablut',
  size: 9,
  board: gameBoard,
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [{ source: 0, target: 1 }],
  mode: 'human-ai',
  players: {
    attackers: { name: 'BundleFixture', kind: 'human', config: {} },
    defenders: {
      name: 'Jarl',
      kind: 'ai',
      config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
    },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
const gameAnalytics = {
  advisor: {
    name: 'Jarl',
    profile_id: 'alpha_beta',
    config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
  },
  time_limit: 5,
  show_win_chances: true,
  revision: 0,
};

/** Read-only synthetic API fixtures shared by every page in the probe. */
async function mockApi(page) {
  const unexpectedMutations = [];
  await page.route('**/api/**', (route) => {
    const method = route.request().method();
    const p = new URL(route.request().url()).pathname;
    // The game page POSTs to kick off analysis; it is a read-only probe fixture.
    if (method === 'POST' && /^\/api\/games\/[^/]+\/analysis$/.test(p))
      return route.fulfill({
        json: { cursor: 0, evaluations: [], hint: null, status: 'complete' },
      });
    if (method !== 'GET') {
      unexpectedMutations.push(method + ' ' + route.request().url());
      return route.abort();
    }
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
              : p === '/api/personal/single-player/review-1/replay'
                ? recordedReview
                : p === '/api/games/g1'
                  ? gameFixture
                  : p === '/api/games/g1/analytics'
                    ? gameAnalytics
                    : p === '/api/personal/progress'
                      ? { streaks: { by_ruleset: [] } }
                      : /^\/api\/games\/[^/]+\/analytics$/.test(p)
                        ? { advisor: null, time_limit: 5, show_win_chances: true, revision: 0 }
                        : /^\/api\/games\/[^/]+\/analysis$/.test(p)
                          ? { cursor: 0, evaluations: [], hint: null, status: 'complete' }
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

const isAsset = (asset) => /^assets\/.*\.(js|css)$/.test(asset);

/**
 * Track every asset request, response and pre-response failure for a page.
 * Recording failures as well as responses means an unexpected failed asset can
 * never disappear silently from the inventory.
 */
function observeAssets(page) {
  const requests = new Map();
  const responses = new Map();
  const failures = new Map();
  page.on('request', (request) => {
    const asset = normalizeAssetPath(request.url());
    if (!isAsset(asset)) return;
    requests.set(asset, (requests.get(asset) || 0) + 1);
  });
  page.on('response', (response) => {
    const asset = normalizeAssetPath(response.url());
    if (!isAsset(asset)) return;
    responses.set(asset, {
      status: response.status(),
      contentType: response.headers()['content-type'] ?? '',
    });
  });
  page.on('requestfailed', (request) => {
    const asset = normalizeAssetPath(request.url());
    if (!isAsset(asset)) return;
    failures.set(asset, request.failure()?.errorText ?? 'request failed');
  });
  return { requests, responses, failures };
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

/**
 * The observation window is the deterministic Dashboard initialization: the
 * document is parsed, the route-selected authenticated graph is requested, and
 * the Dashboard leaves its loading state. The window then holds for a bounded
 * settle period so a delayed eager/feature request can surface before the
 * intentional Workshop navigation. A finite probe cannot prove that an
 * arbitrarily future scheduled import will never occur.
 */
const INITIAL_SETTLE_MS = 750;

async function waitForExpectedGraph(expected, tracker, timeoutMs = 15000) {
  const wanted = [...expected.scripts, ...expected.styles].map(normalizeAssetPath);
  const deadline = Date.now() + timeoutMs;
  let missing = wanted;
  while (Date.now() < deadline) {
    missing = wanted.filter(
      (asset) => !tracker.responses.has(asset) && !tracker.failures.has(asset),
    );
    if (!missing.length) return;
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error('Timed out waiting for the initial manifest graph: ' + missing.join(', '));
}

/** Wait until a specific production asset has produced a response. */
async function waitForAssetResponse(tracker, file, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (tracker.responses.has(file)) return;
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error(`Timed out waiting for asset response: ${file}`);
}

/** A page with the read-only fixtures and asset observation already installed.
 * Each recovery scenario gets its own browser context so a cached stylesheet or
 * asset from an earlier scenario cannot mask a real failure. */
async function fixturePage(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  const mutations = await mockApi(page);
  const tracker = observeAssets(page);
  return {
    page,
    mutations,
    tracker,
    close: async () => {
      await context.close();
    },
  };
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
  const tracker = observeAssets(page);

  await page.goto(origin + '/dashboard');
  await page.locator('.personal-dashboard').waitFor();
  // Deterministic readiness: the Dashboard's own polls have settled rather than
  // an arbitrary network-idle gap. The status badge leaves its loading copy once
  // the mocked account reads complete.
  await page.locator('.dashboard-status-badge').filter({ hasNotText: 'Loading' }).waitFor();
  await waitForExpectedGraph(expected, tracker);
  await page.waitForTimeout(INITIAL_SETTLE_MS);

  const initialRequests = new Set(tracker.requests.keys());
  const initialFailures = new Map(tracker.failures);
  const initialFiles = assets(tracker.responses, '.js');
  const initialCssFiles = assets(tracker.responses, '.css');
  const initialJsRequests = [...initialRequests].filter((asset) => asset.endsWith('.js')).sort();
  const initialCssRequests = [...initialRequests].filter((asset) => asset.endsWith('.css')).sort();
  const jsComparison = compareInitialGraph(expected.scripts, initialJsRequests);
  const cssComparison = compareInitialGraph(expected.styles, initialCssRequests);
  if (jsComparison.unexpected.length)
    throw new Error('Unexpected initial JavaScript: ' + JSON.stringify(jsComparison.unexpected));
  if (jsComparison.missing.length)
    throw new Error('Missing initial JavaScript: ' + JSON.stringify(jsComparison.missing));
  if (cssComparison.unexpected.length)
    throw new Error('Unexpected initial CSS: ' + JSON.stringify(cssComparison.unexpected));
  if (cssComparison.missing.length)
    throw new Error('Missing initial CSS: ' + JSON.stringify(cssComparison.missing));
  // Any failed asset request in the initial window is a real regression.
  const initialFailed = compareAssetOutcomes(initialRequests, tracker.responses, initialFailures);
  if (initialFailed.length)
    throw new Error('Failed initial asset requests: ' + JSON.stringify(initialFailed));
  verifyAssetResponses(tracker.responses, initialFiles, 'js');
  verifyAssetResponses(tracker.responses, initialCssFiles, 'css');

  // Prohibited feature implementations must not appear in any eager chunk,
  // regardless of the generated filename.
  const forbidden = [
    'src/Training.tsx',
    'src/Tournaments.tsx',
    'src/RecordedReview.tsx',
    'src/AgentAnalysis.tsx',
    'src/AdvisorSelect.tsx',
    'src/NewGameForm.tsx',
    'src/AgentConfigFields.tsx',
    'src/StatisticsPage.tsx',
    'src/ProfilePage.tsx',
    'src/Online.tsx',
    'src/PortraitHall.tsx',
    'src/PlayerTournaments.tsx',
    'node_modules/react-markdown/',
  ];
  const eagerFiles = [...expected.scripts].map(normalizeAssetPath);
  const forbiddenModules = findForbiddenModules(ownership.chunks, eagerFiles, forbidden);
  if (forbiddenModules.length)
    throw new Error(
      'Prohibited feature modules in the eager graph: ' + JSON.stringify(forbiddenModules),
    );
  // Keep the lightweight filename-prefix signal as an independent cross-check.
  const unexpectedlyEager = forbidden
    .filter((name) => name.startsWith('src/'))
    .map((name) => name.replace('src/', '').replace('.tsx', ''))
    .filter((name) => initialFiles.some((file) => file.includes(`/${name}-`)));
  if (unexpectedlyEager.length)
    throw new Error('Unexpected eager feature ' + JSON.stringify(unexpectedlyEager));

  const initialInventory = await inventory(initialFiles);
  const initialCssInventory = await inventory(initialCssFiles);

  // Workshop navigation must request its own deferred chunk after first use.
  await page.getByRole('link', { name: 'AI Workshop', exact: true }).click();
  await page.locator('.training-workshop').waitFor();
  const afterTraining = assets(tracker.responses, '.js');
  if (!afterTraining.some((file) => file.includes('/Training-')))
    throw new Error('Training chunk not loaded after Workshop navigation');
  if (afterTraining.some((file) => file.includes('/Tournaments-')))
    throw new Error(
      'Training navigation must not eagerly load Tournaments: ' + JSON.stringify(afterTraining),
    );

  // Production recovery scenarios. Each uses the real hashed assets and their
  // manifest relationships, not mocked development-server module imports.
  const recovery = {};

  // 1. Existing New Game form JS recovery through Retry (preserved).
  const formChunk = Object.values(manifest).find((chunk) => chunk.name === 'NewGameForm')?.file;
  if (!formChunk) throw new Error('NewGameForm chunk missing from the production manifest');
  {
    const {
      page: recoveryPage,
      mutations,
      tracker: recoveryTracker,
      close,
    } = await fixturePage(browser);
    const formName = formChunk.split('/').pop();
    let blockForm = true;
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${formName}`),
      (route) => (blockForm ? route.abort() : route.continue()),
    );
    await recoveryPage.goto(origin + '/dashboard');
    await recoveryPage.locator('.personal-dashboard').waitFor();
    await recoveryPage.getByRole('button', { name: 'Play vs AI', exact: true }).click();
    const recoveryDialog = recoveryPage.getByRole('dialog', {
      name: 'Begin a new saga',
      exact: true,
    });
    await recoveryDialog.getByRole('alert').waitFor();
    blockForm = false;
    await recoveryDialog.getByRole('button', { name: 'Retry', exact: true }).click();
    await recoveryDialog.getByRole('button', { name: 'To the table' }).waitFor();
    if (!assets(recoveryTracker.responses, '.js').includes(formChunk))
      throw new Error('New Game form chunk did not recover through Retry');
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.newGameForm = {
      feature: formChunk,
      firstAttempt: 'aborted',
      recoveredThrough: 'Retry',
    };
  }

  // 2. A failed transitive preload dependency recovers through Retry.
  const dependency = Object.values(manifest).find(
    (chunk) => chunk.name === 'workshopConsolidation',
  );
  if (!dependency)
    throw new Error('workshopConsolidation chunk missing from the production manifest');
  {
    const {
      page: recoveryPage,
      mutations,
      tracker: recoveryTracker,
      close,
    } = await fixturePage(browser);
    const dependencyName = dependency.file.split('/').pop();
    let blockDependency = true;
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${dependencyName}`),
      (route) => (blockDependency ? route.abort() : route.continue()),
    );
    await recoveryPage.goto(origin + '/training');
    await recoveryPage.getByRole('alert').waitFor();
    blockDependency = false;
    await recoveryPage.getByRole('button', { name: 'Retry', exact: true }).click();
    await recoveryPage.locator('.training-workshop').waitFor();
    if (!assets(recoveryTracker.responses, '.js').includes(dependency.file))
      throw new Error('Transitive dependency did not recover through Retry');
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.transitiveDependency = {
      feature: dependency.file,
      firstAttempt: 'aborted',
      recoveredThrough: 'Retry',
    };
  }

  // 3. A deferred stylesheet failure recovers through the explicit Reload and
  //    leaves the recovered content correctly styled.
  const trainingCss = manifest['src/Training.tsx']?.css?.[0];
  if (!trainingCss) throw new Error('Training deferred stylesheet missing from the manifest');
  {
    const { page: recoveryPage, mutations, close } = await fixturePage(browser);
    const cssName = trainingCss.split('/').pop();
    let blockCss = true;
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${cssName}`),
      (route) => (blockCss ? route.abort() : route.continue()),
    );
    await recoveryPage.goto(origin + '/training');
    await recoveryPage.getByRole('alert').waitFor();
    blockCss = false;
    await recoveryPage.getByRole('button', { name: 'Reload application' }).click();
    await recoveryPage.locator('.training-workshop').waitFor();
    const summary = recoveryPage.locator('.workshop-global');
    await summary.waitFor();
    const display = await summary.evaluate((element) => getComputedStyle(element).display);
    if (display !== 'flex')
      throw new Error(`Recovered Training content is not styled (display: ${display})`);
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.deferredStylesheet = {
      feature: trainingCss,
      firstAttempt: 'aborted',
      recoveredThrough: 'Reload application',
      recoveredComputedDisplay: display,
    };
  }

  // 4. A continuously failing stylesheet must not mount an unstyled feature and
  //    must clearly require an explicit reload (same-document Retry is unsafe).
  {
    const { page: recoveryPage, mutations, close } = await fixturePage(browser);
    const cssName = trainingCss.split('/').pop();
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${cssName}`),
      (route) => route.abort(),
    );
    await recoveryPage.goto(origin + '/training');
    const alert = recoveryPage.getByRole('alert');
    await alert.waitFor();
    if (await recoveryPage.getByRole('button', { name: 'Retry', exact: true }).count())
      throw new Error('Retry offered for a same-document-unrecoverable stylesheet failure');
    if (!(await recoveryPage.getByRole('button', { name: 'Reload application' }).isVisible()))
      throw new Error('Reload action missing while the stylesheet is unresolved');
    if (!(await alert.textContent())?.includes('Reload the application to try again'))
      throw new Error('Reload-required feedback missing for a stylesheet failure');
    if (await recoveryPage.locator('.training-workshop').count())
      throw new Error('Training mounted without its stylesheet');
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.stylesheetReloadRequired = {
      feature: trainingCss,
      retryOffered: false,
      featureMounted: false,
      retainedErrorPanel: true,
      recoveredThrough: 'Reload application',
    };
  }

  // 4b. Navigating away and back within the same document must not bypass the
  //     unresolved stylesheet failure.
  {
    const { page: recoveryPage, mutations, close } = await fixturePage(browser);
    const cssName = trainingCss.split('/').pop();
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${cssName}`),
      (route) => route.abort(),
    );
    await recoveryPage.goto(origin + '/training');
    await recoveryPage.getByRole('alert').waitFor();
    await recoveryPage.evaluate(() => {
      history.pushState(null, '', '/dashboard');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await recoveryPage.locator('.personal-dashboard').waitFor();
    await recoveryPage.evaluate(() => {
      history.pushState(null, '', '/training');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await recoveryPage.getByRole('alert').waitFor();
    if (await recoveryPage.locator('.training-workshop').count())
      throw new Error('Training mounted after navigating back despite the unresolved stylesheet');
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.stylesheetNavigation = {
      feature: trainingCss,
      mountedAfterReturn: false,
      retainedErrorPanel: true,
    };
  }

  // 5. A permanently missing hashed JavaScript asset keeps failing through
  //    Retry and recovers only through a fresh document (explicit Reload).
  {
    const { page: recoveryPage, mutations, close } = await fixturePage(browser);
    const formName = formChunk.split('/').pop();
    let blockAsset = true;
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${formName}`),
      (route) => (blockAsset ? route.abort() : route.continue()),
    );
    await recoveryPage.goto(origin + '/dashboard');
    await recoveryPage.locator('.personal-dashboard').waitFor();
    await recoveryPage.getByRole('button', { name: 'Play vs AI', exact: true }).click();
    const dialog = recoveryPage.getByRole('dialog', { name: 'Begin a new saga', exact: true });
    await dialog.getByRole('alert').waitFor();
    await dialog.getByRole('button', { name: 'Retry', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    blockAsset = false;
    await dialog.getByRole('button', { name: 'Reload application' }).click();
    await recoveryPage.locator('.personal-dashboard').waitFor();
    await recoveryPage.getByRole('button', { name: 'Play vs AI', exact: true }).click();
    await recoveryPage
      .getByRole('dialog', { name: 'Begin a new saga', exact: true })
      .getByRole('button', { name: 'To the table' })
      .waitFor();
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.missingHashedAsset = {
      feature: formChunk,
      retriedWhileMissing: true,
      recoveredThrough: 'Reload application',
    };
  }

  // 6. A shared deferred stylesheet (analysis-*.css) is imported by several
  //    features. A failure observed by one importer must block every other
  //    feature that shares it, not only the one that saw the preload event.
  {
    const { page: recoveryPage, mutations, tracker, close } = await fixturePage(browser);
    const sharedCss = manifest['src/Archive.tsx']?.css?.find((file) => file.includes('/analysis-'));
    if (!sharedCss) throw new Error('Shared analysis stylesheet missing from the manifest');
    const sharedName = sharedCss.split('/').pop();
    let blockShared = true;
    await recoveryPage.route(
      (url) => url.pathname.endsWith(`/${sharedName}`),
      (route) => (blockShared ? route.abort() : route.continue()),
    );
    // Archive imports the shared stylesheet directly.
    await recoveryPage.goto(origin + '/archive');
    await recoveryPage.getByRole('alert').waitFor();
    // RecordedReview is a separate deferred import that shares the same asset.
    await recoveryPage.evaluate(() => {
      history.pushState(null, '', '/dashboard/single-player/review-1');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await recoveryPage.getByRole('alert').waitFor();
    if (await recoveryPage.getByRole('grid').count())
      throw new Error('RecordedReview mounted despite the failed shared stylesheet');
    // An unrelated deferred feature is conservatively blocked by the policy.
    await recoveryPage.evaluate(() => {
      history.pushState(null, '', '/training');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await recoveryPage.getByRole('alert').waitFor();
    if (await recoveryPage.locator('.training-workshop').count())
      throw new Error('Training mounted while a shared stylesheet failure is unresolved');
    // Return to the affected feature, then reload to re-request the shared asset.
    await recoveryPage.evaluate(() => {
      history.pushState(null, '', '/dashboard/single-player/review-1');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await recoveryPage.getByRole('alert').waitFor();
    blockShared = false;
    await recoveryPage.getByRole('button', { name: 'Reload application' }).click();
    const settings = recoveryPage.locator('.analytics-settings');
    await settings.waitFor({ state: 'attached' });
    const padding = await settings.evaluate((element) => getComputedStyle(element).paddingTop);
    if (!padding || padding === '0px')
      throw new Error(`Recovered analysis content is not styled (padding-top: ${padding})`);
    const sharedResponse = tracker.responses.get(sharedCss);
    if (!sharedResponse || sharedResponse.status !== 200)
      throw new Error('Shared analysis stylesheet did not respond successfully after reload');
    if (!sharedResponse.contentType.includes('text/css'))
      throw new Error(`Shared analysis stylesheet content-type was ${sharedResponse.contentType}`);
    if (mutations.length)
      throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
    await close();
    recovery.sharedStylesheet = {
      feature: sharedCss,
      blockedImporters: ['Archive', 'RecordedReview'],
      unrelatedFeatureBlocked: true,
      recoveredThrough: 'Reload application',
      recoveredResponse: sharedResponse,
      recoveredComputedPadding: padding,
    };
  }

  // 7. Concurrent deferred imports sharing analysis-*.css: hold the shared
  //    stylesheet pending while the dependent JavaScript imports resolve, then
  //    verify neither feature mounts prematurely and both outcomes are handled.
  {
    const sharedCss = manifest['src/Archive.tsx']?.css?.find((file) => file.includes('/analysis-'));
    const agentChunk = manifest['src/AgentAnalysis.tsx']?.file;
    const advisorChunk = manifest['src/AdvisorSelect.tsx']?.file;
    if (!sharedCss || !agentChunk || !advisorChunk)
      throw new Error('Concurrent analysis assets missing from the manifest');
    const sharedName = sharedCss.split('/').pop();

    // Success: the held stylesheet is released and both features mount styled.
    {
      const { page: recoveryPage, mutations, tracker, close } = await fixturePage(browser);
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      await recoveryPage.route(
        (url) => url.pathname.endsWith(`/${sharedName}`),
        async (route) => {
          await gate;
          await route.continue();
        },
      );
      await recoveryPage.goto(origin + '/games/g1');
      await recoveryPage.getByRole('grid').waitFor();
      // Both JavaScript chunks resolve while the shared stylesheet is pending.
      await waitForAssetResponse(tracker, agentChunk);
      await waitForAssetResponse(tracker, advisorChunk);
      if (await recoveryPage.locator('.agent-analysis, .analytics-settings').count())
        throw new Error('A concurrent feature mounted while the shared stylesheet was pending');
      release();
      const settings = recoveryPage.locator('.analytics-settings');
      await settings.waitFor({ state: 'attached' });
      const padding = await settings.evaluate((element) => getComputedStyle(element).paddingTop);
      if (!padding || padding === '0px')
        throw new Error(`Concurrent recovered content is not styled (padding-top: ${padding})`);
      if (mutations.length)
        throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
      await close();
      recovery.concurrentSharedStylesheetSuccess = {
        feature: sharedCss,
        pendingWhileJsResolved: true,
        mountedWhilePending: false,
        recoveredComputedPadding: padding,
      };
    }

    // Failure: the held stylesheet fails and neither feature may mount.
    {
      const { page: recoveryPage, mutations, tracker, close } = await fixturePage(browser);
      let fail;
      const gate = new Promise((resolve) => {
        fail = resolve;
      });
      await recoveryPage.route(
        (url) => url.pathname.endsWith(`/${sharedName}`),
        async (route) => {
          await gate;
          await route.abort();
        },
      );
      await recoveryPage.goto(origin + '/games/g1');
      await recoveryPage.getByRole('grid').waitFor();
      await waitForAssetResponse(tracker, agentChunk);
      await waitForAssetResponse(tracker, advisorChunk);
      if (await recoveryPage.locator('.agent-analysis, .analytics-settings').count())
        throw new Error('A concurrent feature mounted while the shared stylesheet was pending');
      fail();
      await recoveryPage.locator('[role="alert"]').first().waitFor({ state: 'attached' });
      if (await recoveryPage.locator('.agent-analysis, .analytics-settings').count())
        throw new Error('A concurrent feature mounted after the shared stylesheet failed');
      if (mutations.length)
        throw new Error('Unexpected recovery API mutations: ' + JSON.stringify(mutations));
      await close();
      recovery.concurrentSharedStylesheetFailure = {
        feature: sharedCss,
        mountedWhilePending: false,
        mountedAfterFailure: false,
        retainedErrorPanel: true,
      };
    }
  }

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
    initialWindow: {
      readiness: 'Dashboard left its loading state and the manifest graph responded',
      settleMs: INITIAL_SETTLE_MS,
      limitation:
        'A bounded observation window cannot prove that an arbitrarily future scheduled import never occurs.',
    },
    manifest: {
      authenticatedEntry,
      publicEntry,
      expectedInitialScripts: [...expected.scripts].sort(),
      expectedInitialStyles: [...expected.styles].sort(),
    },
    initialFiles,
    initialCssFiles,
    initialRequests: [...initialRequests].sort(),
    initialFailed,
    initialInventory,
    initialCssInventory,
    initialRaw: initialInventory.reduce((n, f) => n + f.raw, 0),
    initialGzipLevel9: initialInventory.reduce((n, f) => n + f.gzip_level9, 0),
    initialCssRaw: initialCssInventory.reduce((n, f) => n + f.raw, 0),
    initialCssGzipLevel9: initialCssInventory.reduce((n, f) => n + f.gzip_level9, 0),
    observedResponses: Object.fromEntries([...tracker.responses].sort()),
    forbiddenModules,
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
