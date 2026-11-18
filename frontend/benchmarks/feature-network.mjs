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
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const root = resolve(frontend, 'dist');
const output = resolve(process.argv[2] || '/tmp/hnefatafl-feature-network.json');
const source = readGitRevision(repository);
const build = JSON.parse(await readFile(join(root, 'arena-build.json'), 'utf8'));
const checked = verifyBuildRevision(build, source);
if (!checked.ok) throw new Error(checked.message);
const fingerprint = verifyBuildFingerprint(build, await fingerprintProductionBundle(root));
if (!fingerprint.ok) throw new Error(fingerprint.message);
const types = {
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.html': 'text/html',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
};
const server = createServer(async (req, res) => {
  try {
    let path = resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
    if (path !== root && !path.startsWith(root + sep)) throw Error('outside');
    let file;
    try {
      file = await stat(path);
    } catch {
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
let browser;
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
try {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
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
  const files = [];
  page.on('request', (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith('/assets/') && p.endsWith('.js')) files.push(p);
  });
  await page.goto(origin + '/dashboard');
  await page.locator('.personal-dashboard').waitFor();
  const initial = [...new Set(files)];
  const inventory = [];
  for (const name of initial) {
    const bytes = await readFile(resolve(root, '.' + name));
    inventory.push({
      file: name,
      raw: bytes.length,
      gzip_level9: gzipSync(bytes, { level: 9 }).length,
    });
  }
  const forbidden = [
    'Training',
    'Tournaments',
    'RecordedReview',
    'AgentAnalysis',
    'AdvisorSelect',
    'NewGame',
    'StatisticsPage',
    'ProfilePage',
    'Online',
  ];
  if (files.some((f) => forbidden.some((n) => f.includes('/' + n + '-'))))
    throw Error('Unexpected eager feature ' + JSON.stringify(files));
  await page.getByRole('link', { name: 'AI Workshop', exact: true }).click();
  await page.locator('.training-workshop').waitFor();
  if (!files.some((f) => f.includes('/Training-'))) throw Error('Training chunk not loaded');
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
    initialFiles: initial,
    initialInventory: inventory,
    initialRaw: inventory.reduce((n, f) => n + f.raw, 0),
    initialGzipLevel9: inventory.reduce((n, f) => n + f.gzip_level9, 0),
    afterTrainingFiles: [...new Set(files)],
    deferredFeaturesAbsentInitially: forbidden,
  };
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
