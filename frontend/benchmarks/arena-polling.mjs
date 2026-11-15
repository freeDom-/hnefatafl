/** Read-only Arena request baseline against disposable real browser fixtures. */
import { chromium } from 'playwright';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile, access } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir, cpus, release } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const options = {
  output: join(tmpdir(), 'hnefatafl-arena-polling.json'),
  windowMs: 60000,
  warmupMs: 5000,
  port: 4273,
};
for (const arg of process.argv.slice(2)) {
  if (arg === '--help') {
    console.log(
      'Build first: npm --prefix frontend run build\nRun: node frontend/benchmarks/arena-polling.mjs [--output=/tmp/result.json] [--window-ms=60000] [--warmup-ms=5000] [--port=4273]\nRequires installed Playwright Chromium and repository .venv. Uses fixture ports 8001/8002.',
    );
    process.exit(0);
  }
  const [key, ...rest] = arg.split('=');
  const value = rest.join('=');
  if (key === '--output' && value) options.output = resolve(value);
  else if (key === '--window-ms') options.windowMs = Number(value);
  else if (key === '--warmup-ms') options.warmupMs = Number(value);
  else if (key === '--port') options.port = Number(value);
  else throw new Error(`Unknown option: ${arg}`);
}
if (
  !Number.isInteger(options.windowMs) ||
  options.windowMs < 10000 ||
  options.windowMs > 300000 ||
  !Number.isInteger(options.warmupMs) ||
  options.warmupMs < 0 ||
  options.warmupMs > 30000 ||
  !Number.isInteger(options.port) ||
  options.port < 1024 ||
  options.port > 65535 ||
  [8001, 8002].includes(options.port)
)
  throw new Error('Invalid benchmark duration, warmup or port.');
await access(join(frontend, 'dist', 'index.html'));
const python = process.env.ARENA_BENCH_PYTHON || join(repository, '.venv', 'bin', 'python');
await access(python);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function requireFreePort(port) {
  const server = createServer();
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, '127.0.0.1', resolve);
    });
  } finally {
    if (server.listening) await new Promise((resolve) => server.close(resolve));
  }
}
for (const port of [8001, 8002, options.port]) await requireFreePort(port);
const dataDirectory = await mkdtemp(join(tmpdir(), 'hnefatafl-browser-arena-audit-'));
const children = [];
let browser;
let signalError;
const interrupt = (signal) => {
  signalError = new Error(`Benchmark interrupted: ${signal}`);
};
const onInterrupt = () => interrupt('SIGINT');
const onTerminate = () => interrupt('SIGTERM');
process.on('SIGINT', onInterrupt);
process.on('SIGTERM', onTerminate);
function start(command, args) {
  const child = spawn(command, args, {
    cwd: frontend,
    env: {
      ...process.env,
      HNEFATAFL_DATA_DIR: dataDirectory,
      HNEFATAFL_ENV: 'development',
      VITE_API_TARGET: 'http://127.0.0.1:8001',
      OMP_NUM_THREADS: '2',
      MKL_NUM_THREADS: '2',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let tail = '';
  child.stdout.on('data', (data) => {
    tail = (tail + data).slice(-12000);
  });
  child.stderr.on('data', (data) => {
    tail = (tail + data).slice(-12000);
  });
  child.on('error', (error) => {
    signalError = error;
  });
  children.push({ child, log: () => tail });
  return child;
}
function checkChildren() {
  if (signalError) throw signalError;
  for (const { child, log } of children) {
    if (child.exitCode !== null || child.signalCode !== null)
      throw new Error(`Fixture service exited (${child.exitCode ?? child.signalCode}): ${log()}`);
  }
}
async function wait(ms) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    checkChildren();
    await sleep(Math.min(250, deadline - Date.now()));
  }
}
async function ready(url) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    checkChildren();
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).ok) return;
    } catch {
      /* bounded startup retry */
    }
    await wait(250);
  }
  throw new Error(`Service not ready after 60s: ${url}`);
}
try {
  start(python, ['tests/start_backend.py']);
  start(process.execPath, [
    join(frontend, 'node_modules', 'vite', 'bin', 'vite.js'),
    'preview',
    '--host',
    '127.0.0.1',
    '--port',
    String(options.port),
    '--strictPort',
  ]);
  const origin = `http://127.0.0.1:${options.port}`;
  await Promise.all([ready('http://127.0.0.1:8001/api/health'), ready(origin)]);
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const login = await context.request.post('http://127.0.0.1:8001/api/auth/login', {
    headers: { Origin: 'http://127.0.0.1:8001' },
    data: { identity: 'BrowserAdmin', password: 'browser-fixture-only-password' },
  });
  if (!login.ok()) throw new Error(`Fixture login failed: ${login.status()}`);
  const session = await login.json();
  const page = await context.newPage();
  let phase = 'empty-startup';
  const phases = {};
  const pending = new WeakMap();
  const normalize = (url) =>
    url.pathname.replace(/\/games\/[A-Za-z0-9_-]+$/, '/games/:id') + url.search;
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return;
    const endpoint = `${request.method()} ${normalize(url)}`;
    phases[phase] ??= { requests: 0, endpoints: {}, failed: 0, statuses: {} };
    const sample = phases[phase];
    sample.requests++;
    sample.endpoints[endpoint] = (sample.endpoints[endpoint] || 0) + 1;
    pending.set(request, sample);
  });
  page.on('requestfailed', (request) => {
    const sample = pending.get(request);
    if (sample) sample.failed++;
  });
  page.on('response', (response) => {
    const sample = pending.get(response.request());
    if (sample) sample.statuses[response.status()] = (sample.statuses[response.status()] || 0) + 1;
  });
  await page.goto(`${origin}/dashboard`);
  await page.locator('.personal-dashboard').waitFor({ timeout: 30000 });
  await wait(options.warmupMs);
  phase = 'empty-visible';
  await wait(options.windowMs);
  const game = await context.request.post(`${origin}/api/games/local`, {
    headers: { Origin: origin, 'X-CSRF-Token': session.csrf_token },
    data: {
      ruleset: 'brandubh',
      attackers_label: 'Audit attacker',
      defenders_label: 'Audit defender',
    },
  });
  if (!game.ok()) throw new Error(`Fixture local game creation failed: ${game.status()}`);
  phase = 'active-startup';
  await page.reload();
  await page.locator('.personal-dashboard .board').first().waitFor({ timeout: 30000 });
  await wait(options.warmupMs);
  phase = 'active-visible';
  await wait(options.windowMs);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  phase = 'active-simulated-hidden';
  await wait(options.windowMs);
  const report = {
    schema: 1,
    sourceHead: execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: repository,
      encoding: 'utf8',
      timeout: 5000,
    }).trim(),
    measuredAt: new Date().toISOString(),
    node: process.version,
    chromium: browser.version(),
    os: release(),
    logicalCpus: cpus().length,
    viewport: { width: 1920, height: 1080 },
    frontend: 'production Vite preview of existing frontend/dist; build before running',
    fixture:
      'isolated real browser fixture backend, authenticated administrator; empty, then one active same-device Brandubh game',
    sampleWindowMs: options.windowMs,
    warmupMs: options.warmupMs,
    scope:
      'Browser page /api/ request starts only; setup APIRequestContext traffic excluded. Normalized identifiers; no payloads or credentials recorded.',
    hiddenCaveat:
      'document.visibilityState override and visibilitychange signal test app policy; this is not real OS background throttling or a physical device.',
    phases,
  };
  await mkdir(dirname(options.output), { recursive: true });
  await writeFile(options.output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser?.close().catch(() => {});
  for (const { child } of children)
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  await Promise.all(
    children.map(async ({ child }) => {
      for (let i = 0; i < 40 && child.exitCode === null && child.signalCode === null; i++)
        await sleep(100);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      if (child.exitCode === null && child.signalCode === null)
        await new Promise((resolve) => child.once('exit', resolve));
    }),
  );
  await rm(dataDirectory, { recursive: true, force: true });
  process.removeListener('SIGINT', onInterrupt);
  process.removeListener('SIGTERM', onTerminate);
}
