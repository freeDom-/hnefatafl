// Run after the production build. Serve compiled assets and the real API from
// the same disposable loopback backend used by online browser tests.
import { chromium } from 'playwright';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { setTimeout as delay } from 'node:timers/promises';
import {
  fingerprintProductionBundle,
  readGitRevision,
  verifyBuildFingerprint,
  verifyBuildRevision,
} from './buildRevision.mjs';
import { collectInitialGraph, routeChunk } from '../scripts/bundleGraph.mjs';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const dist = join(frontend, 'dist');
const output = process.argv.slice(2).find((argument) => !argument.startsWith('--'));
const sampleSeconds = 17;
const steadyIdleSeconds = 65;
const allowDirty = process.argv.includes('--allow-dirty');
const build = JSON.parse(await readFile(join(dist, 'arena-build.json'), 'utf8'));
const revision = verifyBuildRevision(build, readGitRevision(repository), { allowDirty });
if (!revision.ok) throw new Error(revision.message);
const fingerprint = verifyBuildFingerprint(build, await fingerprintProductionBundle(dist));
if (!fingerprint.ok) throw new Error(fingerprint.message);
const manifest = JSON.parse(await readFile(join(dist, '.vite', 'manifest.json'), 'utf8'));
const league = routeChunk(manifest, 'Leagues')[1];
const initial = collectInitialGraph(manifest);
if (initial.scripts.has(league.file)) throw new Error('League route chunk is eager');

async function sizes(files) {
  return Promise.all(
    [...files].sort().map(async (file) => {
      const raw = await readFile(join(dist, file));
      return { file, bytes: raw.length, gzip_bytes: gzipSync(raw, { level: 9 }).length };
    }),
  );
}

async function loopbackPort() {
  const server = createServer().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  await new Promise((done, fail) => server.close((error) => (error ? fail(error) : done())));
  return port;
}

async function waitForHealth(child, origin) {
  for (let attempt = 0; attempt < 100 && child.exitCode == null; attempt++) {
    const response = await fetch(`${origin}/api/health`, {
      signal: AbortSignal.timeout(1000),
    }).catch(() => null);
    if (response?.ok) return;
    await delay(200);
  }
  throw new Error('Disposable League benchmark backend did not become healthy');
}

function group(rows) {
  const records = {};
  for (const path of [...new Set(rows.map((row) => row.path))].sort()) {
    const matching = rows.filter((row) => row.path === path);
    const finished = matching.filter((row) => row.milliseconds !== undefined);
    const times = finished.map((row) => row.milliseconds).sort((a, b) => a - b);
    const p = (fraction) =>
      times.length ? Number(times[Math.ceil(times.length * fraction) - 1].toFixed(3)) : null;
    records[path] = {
      requests: matching.length,
      completed: finished.length,
      http_errors: matching.filter((row) => row.status >= 400).length,
      network_errors: matching.filter((row) => row.failed && !row.cancelled).length,
      cancelled: matching.filter((row) => row.cancelled).length,
      bytes: finished.reduce((sum, row) => sum + row.bytes, 0),
      latency_ms: { p50: p(0.5), p95: p(0.95), p99: p(0.99) },
    };
  }
  return records;
}

const port = await loopbackPort();
const controlPort = await loopbackPort();
const origin = `http://127.0.0.1:${port}`;
const fixture = await mkdtemp(join(tmpdir(), 'hnefatafl-online-browser-'));
const isolatedEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith('HNEFATAFL_')),
);
const child = spawn(join(repository, '.venv', 'bin', 'python'), ['tests/start_online_backend.py'], {
  cwd: frontend,
  env: {
    ...isolatedEnvironment,
    HNEFATAFL_DATA_DIR: fixture,
    HNEFATAFL_ENV: 'development',
    HNEFATAFL_PUBLIC_URL: origin,
    HNEFATAFL_ONLINE_FIXTURE_PORT: String(port),
    HNEFATAFL_ONLINE_FIXTURE_CONTROL_PORT: String(controlPort),
  },
  stdio: 'ignore',
});
const exited = once(child, 'exit');
let browser;
try {
  await waitForHealth(child, origin);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
  const context = await browser.newContext({ baseURL: origin, reducedMotion: 'reduce' });
  const login = await context.request.post('/api/auth/login', {
    headers: { Origin: origin },
    data: { identity: 'LeagueSolochromium', password: 'online-browser-fixture-password' },
  });
  if (!login.ok()) throw new Error(`Fixture login failed with HTTP ${login.status()}`);
  const page = await context.newPage();
  const network = await context.newCDPSession(page);
  await network.send('Network.enable');
  const requests = new Map();
  let phase = 'dashboard';
  network.on('Network.requestWillBeSent', ({ requestId, request, timestamp }) => {
    const url = new URL(request.url);
    if (url.origin !== origin) return;
    requests.set(requestId, { path: url.pathname, phase, timestamp, method: request.method });
  });
  network.on('Network.responseReceived', ({ requestId, response }) => {
    const row = requests.get(requestId);
    if (row) row.status = response.status;
  });
  network.on('Network.loadingFinished', ({ requestId, timestamp, encodedDataLength }) => {
    const row = requests.get(requestId);
    if (row) {
      row.milliseconds = (timestamp - row.timestamp) * 1000;
      row.bytes = encodedDataLength;
    }
  });
  network.on('Network.loadingFailed', ({ requestId, canceled }) => {
    const row = requests.get(requestId);
    if (row) {
      row.failed = true;
      row.cancelled = canceled;
    }
  });
  await page.goto('/dashboard');
  await page.locator('.personal-dashboard').waitFor();
  if ([...requests.values()].some((row) => row.path === `/${league.file}`)) {
    throw new Error('League chunk requested before navigating to League');
  }
  phase = 'first_visit';
  await page.locator('nav:visible [data-destination="leagues"]').click();
  const joinQueue = page.getByRole('button', { name: 'Join queue', exact: true });
  await joinQueue.waitFor();
  await page.waitForLoadState('networkidle');
  phase = 'idle';
  await delay(sampleSeconds * 1000);
  phase = 'steady_idle';
  await delay((steadyIdleSeconds - sampleSeconds) * 1000);
  phase = 'refresh';
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/leagues/queue'),
    page.getByRole('button', { name: 'Refresh leagues', exact: true }).click(),
  ]);
  await page.waitForLoadState('networkidle');
  phase = 'join';
  await joinQueue.click();
  await page.getByText('Waiting for an opponent', { exact: true }).waitFor();
  phase = 'queued';
  await delay(sampleSeconds * 1000);
  phase = 'leave';
  await page.locator('nav:visible [data-destination="dashboard"]').click();
  await page.locator('.personal-dashboard').waitFor();
  await delay(4500);
  const rows = [...requests.values()];
  if (!rows.some((row) => row.phase === 'first_visit' && row.path === `/${league.file}`)) {
    throw new Error('Production League chunk was never requested');
  }
  const leagueRequests = rows.filter((row) => row.path.startsWith('/api/leagues/'));
  const phases = Object.fromEntries(
    ['first_visit', 'idle', 'steady_idle', 'refresh', 'join', 'queued', 'leave'].map((key) => [
      key,
      group(leagueRequests.filter((row) => row.phase === key)),
    ]),
  );
  if (Object.keys(phases.idle).length || Object.keys(phases.leave).length) {
    throw new Error('League API requests exceeded the short idle or route-leave budget');
  }
  if (
    Object.keys(phases.steady_idle).join() !== '/api/leagues/queue' ||
    phases.steady_idle['/api/leagues/queue'].requests !== 1
  ) {
    throw new Error(
      'Expected one shared queue availability check during the first 65 seconds idle',
    );
  }
  const refreshRequests = Object.values(phases.refresh).reduce(
    (count, entry) => count + entry.requests,
    0,
  );
  if (refreshRequests !== 5 || leagueRequests.filter((row) => row.method !== 'GET').length !== 1) {
    throw new Error(
      'The shared refresh should read five projections and leave queue intentions explicit',
    );
  }
  if ((phases.queued['/api/leagues/queue']?.completed ?? 0) < 2) {
    throw new Error('Queue status polling was not observed');
  }
  if (leagueRequests.some((row) => row.status >= 400 || (row.failed && !row.cancelled))) {
    throw new Error('The browser observed a League API error');
  }
  const document = {
    build_revision: build,
    chromium: browser.version(),
    node: process.version,
    gzip_level: 9,
    sample_seconds_per_idle_or_queued_phase: sampleSeconds,
    sample_seconds_total_idle: steadyIdleSeconds,
    sample_seconds_after_route_leave: 4.5,
    league_api: phases,
    first_visit_feature_transfer: group(
      rows.filter((row) => row.phase === 'first_visit' && row.path.startsWith('/assets/')),
    ),
    route_assets: await sizes([league.file, ...(league.css ?? [])]),
    initial_assets: {
      scripts: await sizes(initial.scripts),
      styles: await sizes(initial.styles),
    },
  };
  const payload = JSON.stringify(document, null, 2) + '\n';
  if (output) await writeFile(resolve(output), payload);
  else process.stdout.write(payload);
} finally {
  await browser?.close();
  if (child.exitCode == null) child.kill('SIGTERM');
  try {
    const stopped = await Promise.race([
      exited.then(() => true),
      delay(5000, false, { ref: false }),
    ]);
    if (!stopped) {
      child.kill('SIGKILL');
      await exited;
    }
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
}
