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
const expectRuntimeId = argument('expect-runtime-id');
const previousManifestPath = argument('previous-manifest');
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
  const previousManifest = previousManifestPath
    ? JSON.parse(await readFile(resolve(previousManifestPath), 'utf8'))
    : null;
  const evaluated = await page.evaluate(
    async ({ entry, state, settings, neural, expectRuntimeId, previousManifest }) => {
      const module = await import(entry);
      const api = await module.createBrowserAnalysis();
      const availability = await api.availability();
      const started = performance.now();
      const status = await api.initialize();
      const initialized = performance.now();
      const result = await api.runSearch({ state, settings, algorithm: 'jarl' });
      const finished = performance.now();
      let previousRuntimeAsset = null;
      if (previousManifest) {
        const path = previousManifest.files.find((entry) => entry.path === 'runtime/pyodide.js');
        const response = await fetch(
          `/browser-analysis/runtime/${previousManifest.runtime_id}/runtime/pyodide.js`,
          { cache: 'no-store', credentials: 'same-origin' },
        );
        previousRuntimeAsset = {
          status: response.status,
          digest: response.headers.get('x-content-sha256'),
          bytes: (await response.arrayBuffer()).byteLength,
          expected: path?.sha256 ?? null,
          expectedBytes: path?.bytes ?? null,
          runtimeId: previousManifest.runtime_id,
        };
      }
      let neuralResult = null;
      let neuralSecond = null;
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
          algorithm: 'neural_mcts',
        });
        neuralSecond = await api.runSearch({
          state,
          settings: { iterations: 8, time_limit: 1, seed: 7 },
          model,
          provider: 'wasm',
          algorithm: 'neural_mcts',
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
        neuralSecond,
        neuralModel,
        previousRuntimeAsset,
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
      settings: { depth: 3, time_limit: 0.5, seed: 7 },
      expectRuntimeId,
      previousManifest,
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
      evaluated.neuralResult?.evidence?.requestedProvider === 'wasm' &&
        evaluated.neuralResult?.evidence?.observedPlacement === 'wasm' &&
        evaluated.neuralResult?.evidence?.gpuExecutionVerified === false &&
        evaluated.neuralResult?.evidence?.inferenceObserved === true,
      JSON.stringify(evaluated.neuralResult?.evidence ?? null),
    );
    check(
      'neural search reports completed simulations and effective settings',
      Number.isInteger(evaluated.neuralResult?.evidence?.completedSimulations) &&
        evaluated.neuralResult?.settings?.iterations === 32,
      JSON.stringify(evaluated.neuralResult?.settings ?? null),
    );
    check(
      'a second neural search reuses the verified model without another transfer',
      Boolean(evaluated.neuralSecond?.analysis),
    );
    report.neural = {
      model: evaluated.neuralResult?.model ?? null,
      evidence: evaluated.neuralResult?.evidence ?? null,
      settings: evaluated.neuralResult?.settings ?? null,
      analysis: evaluated.neuralResult?.analysis ?? null,
    };
  }

  check('availability reports an enabled runtime', evaluated.availability.enabled === true);
  if (expectRuntimeId) {
    check(
      'activated runtime identity is observed end-to-end',
      evaluated.availability.runtime?.runtimeId === expectRuntimeId &&
        evaluated.status.runtimeId === expectRuntimeId,
      JSON.stringify({ expected: expectRuntimeId, actual: evaluated.status.runtimeId }),
    );
  }
  check('runtime initializes', evaluated.status.state === 'ready', JSON.stringify(evaluated.status));
  check('engine identity is reported', Boolean(evaluated.status.engine));
  check(
    'Jarl search completes with a legal advisory analysis',
    Boolean(evaluated.result?.analysis) && evaluated.result.authoritative === false,
  );
  check(
    'Jarl reports the explicit alpha-beta algorithm and effective settings',
    evaluated.result?.algorithm === 'jarl' &&
      evaluated.result?.settings?.algorithm === 'alpha_beta' &&
      evaluated.result?.settings?.depth === 3,
    JSON.stringify(evaluated.result?.settings ?? null),
  );
  check(
    'Jarl has no neural inference evidence',
    evaluated.result?.evidence?.inferenceObserved === false &&
      evaluated.result?.evidence?.observedPlacement === 'none',
    JSON.stringify(evaluated.result?.evidence ?? null),
  );
  check('no neural model was required for Jarl', evaluated.result?.model === null);
  check(
    'classical Jarl has no neural provider claim',
    evaluated.result?.evidence?.gpuExecutionVerified === false &&
      evaluated.result?.evidence?.requestedProvider === null,
  );
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
    modelArtifacts: observed.filter(
      (url) => url.includes('/api/browser-analysis/models/') && url.includes('/artifact'),
    ).length,
    modelMetadata: observed.filter(
      (url) =>
        url.includes('/api/browser-analysis/models/') && !url.includes('/artifact'),
    ).length,
    ordinaryPage: ordinaryRequests.length,
  };
  if (neural) {
    check(
      'one cold model artifact transfer is sufficient for initialization and reuse',
      report.requests.modelArtifacts === 1,
      JSON.stringify(report.requests),
    );
  }
  if (evaluated.previousRuntimeAsset) {
    const asset = evaluated.previousRuntimeAsset;
    check(
      'a retained runtime identity still serves its own immutable bytes',
      asset.status === 200 &&
        asset.digest === asset.expected &&
        asset.bytes === asset.expectedBytes,
      JSON.stringify(asset),
    );
    report.previousRuntime = asset;
  }
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
