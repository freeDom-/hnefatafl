/**
 * Production browser-analysis runtime verification.
 *
 * Runs against the real FastAPI-served production frontend and the real
 * production CSP: logs in, confirms ordinary pages load nothing from the
 * browser-analysis runtime, imports the lazy Phase 4 contract chunk, verifies the
 * runtime assets, initializes Pyodide in a worker, runs one Jarl search and
 * disposes the runtime.
 *
 * Usage:
 *   node scripts/verify_browser_analysis.mjs --base-url <url> --dist <dir>
 *     --state <state.json> [--output <report.json>]
 */
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

function argument(name, fallback = null) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const baseUrl = (argument('base-url') ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const dist = resolve(argument('dist', 'frontend/dist'));
const statePath = argument('state');
const output = argument('output');
const neural = process.argv.includes('--neural');
const identity = argument('identity', process.env.BROWSER_ANALYSIS_IDENTITY ?? 'Browser');
const password = argument('password', process.env.BROWSER_ANALYSIS_PASSWORD);
if (!password) throw new Error('Provide --password or BROWSER_ANALYSIS_PASSWORD');
if (!statePath) throw new Error('Provide --state <position.json>');

const { chromium } = await import(
  pathToFileURL(resolve('frontend/node_modules/playwright/index.mjs')).href
);
const manifest = JSON.parse(await readFile(resolve(dist, '.vite', 'manifest.json'), 'utf8'));
const entry = manifest['src/browserAnalysis/entry.ts'];
if (!entry?.file) throw new Error('The browser-analysis contract chunk is not in the build');

const state = JSON.parse(await readFile(resolve(statePath), 'utf8'));
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
const report = {
  schema: 1,
  baseUrl,
  browser: browser.version(),
  entry: `/${entry.file}`,
  checks: [],
  requests: {},
};
const results = [];
function check(name, ok, detail = null) {
  results.push({ name, ok: Boolean(ok), detail });
  report.checks.push({ name, ok: Boolean(ok), detail });
  if (!ok) throw new Error(`check failed: ${name}${detail ? ` (${detail})` : ''}`);
}

try {
  const context = await browser.newContext();
  const login = await context.request.post(`${baseUrl}/api/auth/login`, {
    data: { identity, password },
    headers: { Origin: baseUrl, 'Content-Type': 'application/json' },
  });
  check('login succeeds', login.status() === 200, `status ${login.status()}`);
  const page = await context.newPage();
  const observed = [];
  page.on('request', (request) => observed.push(request.url()));

  const document = await page.goto(`${baseUrl}/`, { waitUntil: 'load' });
  check('production document loads', document?.status() === 200, `status ${document?.status()}`);
  const policy = document.headers()['content-security-policy'] ?? '';
  check('CSP allows WASM without broad eval', policy.includes("'wasm-unsafe-eval'"));
  check('CSP has no broad unsafe-eval', !policy.includes("'unsafe-eval'"));
  check('CSP restricts workers to self', policy.includes("worker-src 'self'"), policy);
  const ordinaryRequests = [...observed];
  check(
    'ordinary page loads no runtime assets',
    !ordinaryRequests.some((url) => url.includes('/browser-analysis/runtime/')),
  );
  check(
    'ordinary page loads no model artifacts',
    !ordinaryRequests.some((url) => url.includes('/api/browser-analysis/models/')),
  );

  observed.length = 0;
  const evaluated = await page.evaluate(
    async ({ entry, state, settings, neural }) => {
      const module = await import(entry);
      const api = await module.createBrowserAnalysis();
      const availability = await api.availability();
      const started = performance.now();
      const status = await api.initialize();
      const initialized = performance.now();
      const result = await api.runSearch({ state, settings });
      const finished = performance.now();
      let neuralResult = null;
      let neuralModel = null;
      if (neural) {
        const models = await api.listModels();
        if (models.length === 0) throw new Error('No published model is available');
        const model = models[0];
        await api.downloadModel(model, {});
        neuralModel = model;
        neuralResult = await api.runSearch({
          state,
          settings: { iterations: 32, time_limit: 1, seed: 7 },
          model,
          provider: 'wasm',
        });
      }
      const usage = await api.storageUsage();
      const cycles = {
        coldInitMs: initialized - started,
        jarlSearchMs: finished - initialized,
      };
      // A cached initialization only re-verifies assets; it never downloads them.
      const secondStart = performance.now();
      await api.initialize();
      const second = performance.now();
      cycles.secondInitMs = second - secondStart;
      await api.dispose();
      return {
        availability,
        status,
        result,
        neuralResult,
        neuralModel,
        usage,
        cycles,
        hasWorker: typeof Worker === 'function',
        sharedArrayBuffer: typeof SharedArrayBuffer,
      };
    },
    {
      entry: `/${entry.file}`,
      state,
      neural,
      settings: { algorithm: 'alpha_beta', depth: 3, time_limit: 0.5, seed: 7 },
    },
  );
  if (neural) {
    check(
      'published model runs neural MCTS in the browser',
      Boolean(evaluated.neuralResult?.analysis) && evaluated.neuralResult.authoritative === false,
      JSON.stringify(evaluated.neuralResult?.analysis ?? null),
    );
    check(
      'neural result is bound to the published model digest',
      Boolean(evaluated.neuralModel?.onnxSha256) &&
        evaluated.neuralResult?.model?.sha256 === evaluated.neuralModel?.onnxSha256 &&
        evaluated.neuralResult?.model?.ruleset === evaluated.neuralModel?.ruleset,
    );
    check(
      'neural provider reporting is honest',
      evaluated.neuralResult?.provider?.requested === 'wasm' &&
        evaluated.neuralResult?.provider?.gpuExecutionVerified === false,
    );
    report.neural = {
      model: evaluated.neuralResult?.model ?? null,
      provider: evaluated.neuralResult?.provider ?? null,
      analysis: evaluated.neuralResult?.analysis ?? null,
    };
  }

  check('availability reports an enabled runtime', evaluated.availability.enabled === true);
  check('runtime initializes', evaluated.status.state === 'ready', JSON.stringify(evaluated.status));
  check('engine identity is reported', Boolean(evaluated.status.engine));
  check(
    'Jarl search completes with a legal advisory analysis',
    Boolean(evaluated.result?.analysis) && evaluated.result.authoritative === false,
  );
  check('no neural model was required for Jarl', evaluated.result?.model === null);
  check('provider placement is honest', evaluated.result?.provider?.gpuExecutionVerified === false);
  check(
    'runtime assets came from the application origin',
    observed
      .filter((url) => url.includes('/browser-analysis/runtime/'))
      .every((url) => url.startsWith(baseUrl)),
  );
  check(
    'no external runtime origins',
    observed.every((url) => url.startsWith(baseUrl) || url.startsWith('blob:') || url.startsWith('data:')),
  );
  check(
    'no automatic model download during Jarl',
    neural || !observed.some((url) => url.includes('/api/browser-analysis/models/')),
  );
  report.requests = {
    runtimeAssets: observed.filter((url) => url.includes('/browser-analysis/runtime/')).length,
    modelArtifacts: observed.filter((url) => url.includes('/api/browser-analysis/models/')).length,
    ordinaryPage: ordinaryRequests.length,
  };
  report.runtime = {
    runtimeId: evaluated.status.runtimeId,
    pins: evaluated.availability.runtime?.pins ?? null,
    engine: evaluated.status.engine,
    cycles: evaluated.cycles,
    storage: evaluated.usage,
    provider: evaluated.result?.provider ?? null,
    analysis: evaluated.result?.analysis ?? null,
    hasWorker: evaluated.hasWorker,
    crossOriginIsolated: evaluated.sharedArrayBuffer !== 'undefined',
  };
  report.ok = results.every((entry) => entry.ok);
  await context.close();
} finally {
  await browser.close();
}

if (output) await writeFile(output, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 1);
