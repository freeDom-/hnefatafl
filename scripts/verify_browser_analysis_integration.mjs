/**
 * Production browser-first integration verification (Phase 5).
 *
 * Runs against a real FastAPI-served production Vite build, real CSP, real
 * workers/WASM, a verified staged runtime and an explicitly published model.
 * It drives the same integration module the application lazily loads, then
 * disconnects the browser from the network and repeats warm local searches.
 *
 * Usage:
 *   node scripts/verify_browser_analysis_integration.mjs --base-url <url>
 *     --dist <dir> [--output <report.json>]
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

function argument(name, fallback = null) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const baseUrl = (argument('base-url') ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const dist = resolve(argument('dist', 'frontend/dist'));
const output = argument('output');
const screenshots = argument('screenshots');
const password = argument('password', process.env.BROWSER_ANALYSIS_PASSWORD);
if (!password) throw new Error('Provide --password or BROWSER_ANALYSIS_PASSWORD');

const { chromium } = await import(
  pathToFileURL(resolve('frontend/node_modules/playwright/index.mjs')).href
);
const manifest = JSON.parse(await readFile(resolve(dist, '.vite', 'manifest.json'), 'utf8'));
const integration = manifest['src/browserAnalysis/integration.ts'];
if (!integration?.file) throw new Error('The integration chunk is not in the build');

const report = { schema: 1, baseUrl, checks: [], requests: {}, measurements: {} };
const results = [];
function check(name, ok, detail = null) {
  report.checks.push({ name, ok: Boolean(ok), detail });
  results.push(ok);
  if (!ok) throw new Error(`check failed: ${name}${detail ? ` (${JSON.stringify(detail)})` : ''}`);
}

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
try {
  const context = await browser.newContext();
  const login = await context.request.post(`${baseUrl}/api/auth/login`, {
    data: { identity: 'admin', password },
    headers: { Origin: baseUrl, 'Content-Type': 'application/json' },
  });
  check('login succeeds', login.status() === 200, `status ${login.status()}`);
  const session = await login.json();
  const headers = {
    Origin: baseUrl,
    'Content-Type': 'application/json',
    'X-CSRF-Token': session.csrf_token,
  };

  const created = await context.request.post(`${baseUrl}/api/games`, {
    data: {
      ruleset: 'brandubh',
      mode: 'human-ai',
      human_side: 'attackers',
      players: { defenders: { profile_id: 'baseline' } },
    },
    headers,
  });
  check('account game is created', created.status() === 201, await created.text());
  let game = await created.json();
  const models = await context.request.get(`${baseUrl}/api/models`, { headers });
  const modelList = await models.json();
  const registered = modelList.find((entry) => entry.id);
  // The publication is bound to the smoke checkpoint through its weight
  // fingerprint; the server exposes the mapping in the browser model catalog.
  const browserModels = await context.request.get(`${baseUrl}/api/browser-analysis/models`, {
    headers,
  });
  const published = (await browserModels.json()).models[0];
  const registeredId = published.registered_model_ids?.[0] ?? registered?.id;

  const jarlAdvisor = {
    name: 'Jarl advisor',
    profile_id: 'alpha_beta',
    config: { algorithm: 'alpha_beta', depth: 3, time_limit: 1, heuristic: 'shield_maiden' },
  };
  const jarlSettings = await context.request.put(`${baseUrl}/api/games/${game.id}/analytics`, {
    data: { advisor: jarlAdvisor, time_limit: 5, show_win_chances: true },
    headers,
  });
  check('Jarl analytics settings are saved', jarlSettings.status() === 200);
  const jarlExecution = (await jarlSettings.json()).execution;
  check('server policy is browser-first', jarlExecution.mode === 'browser_preferred');

  const page = await context.newPage();
  const pageRequests = [];
  const pageRequestDetails = [];
  page.on('request', (request) => {
    pageRequests.push(request.url());
    pageRequestDetails.push({ url: request.url(), method: request.method() });
  });
  const document = await page.goto(`${baseUrl}/`, { waitUntil: 'load' });
  check('production document loads', document?.status() === 200, document?.status());
  check(
    'ordinary game page does not initialize the runtime',
    !pageRequests.some((url) => url.includes('/browser-analysis/runtime/')),
  );

  const metricsBefore = await context.request.get(`${baseUrl}/api/analysis/metrics`, { headers });
  check('server analysis metrics are available', metricsBefore.status() === 200);
  const before = await metricsBefore.json();

  const loadData = async () => {
    const gameResponse = await context.request.get(`${baseUrl}/api/games/${game.id}`, { headers });
    const settingsResponse = await context.request.get(
      `${baseUrl}/api/games/${game.id}/analytics`,
      { headers },
    );
    check('analysis context data is readable', gameResponse.status() === 200 && settingsResponse.status() === 200);
    return { game: await gameResponse.json(), settings: await settingsResponse.json() };
  };

  const run = async (data, offline) =>
    page.evaluate(
      async ({ integrationPath, data, offline }) => {
        const integration = await import(integrationPath);
        const policy = integration.normalizeExecutionPolicy(data.settings.execution);
        await integration.setAnalysisAccount('account');
        const controller = new AbortController();
        const started = performance.now();
        const outcome = await integration.runLocalAnalysis({
          accountId: 'account',
          game: data.game,
          basePath: '/games',
          cursor: data.game.cursor,
          settings: data.settings,
          policy,
          signal: controller.signal,
          offline,
          onStage: () => undefined,
        });
        return {
          ms: performance.now() - started,
          report: outcome.report,
          evidence: outcome.evidence,
          provider: outcome.provider,
          offline: outcome.offline,
          model: outcome.model,
        };
      },
      {
        integrationPath: `/${integration.file}`,
        data,
        offline,
      },
    );

  const jarlData = await loadData();
  const jarl = await run(jarlData, false);
  check(
    'Jarl executes through the application integration layer',
    jarl.report.evaluations[0]?.config.algorithm === 'alpha_beta' &&
      jarl.report.evaluations[0]?.completed_depth === 3 &&
      jarl.report.status === 'complete',
    jarl.report,
  );
  check('Jarl runs without neural inference', jarl.evidence.inferenceObserved === false);
  report.measurements.jarlOnlineMs = jarl.ms;

  // Switch the requested advisor to the published neural model and execute the
  // same transparent path. No agent or strength substitution occurs.
  const neuralSettings = await context.request.put(`${baseUrl}/api/games/${game.id}/analytics`, {
    data: {
      advisor: {
        name: published.display_name,
        profile_id: 'neural',
        config: {
          algorithm: 'neural',
          model_id: registeredId,
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          temperature: 0,
        },
      },
      time_limit: 5,
      show_win_chances: true,
    },
    headers,
  });
  check('neural analytics settings are saved', neuralSettings.status() === 200, await neuralSettings.text());
  const neuralData = await loadData();
  const neural = await run(neuralData, false);
  check(
    'neural MCTS executes through the application integration layer',
    neural.report.evaluations[0]?.source === 'neural_search' &&
      neural.report.evaluations[0]?.simulations === 32 &&
      neural.model?.sha256 === published.onnx_sha256,
    neural.report,
  );
  check(
    'provider evidence remains honest',
    neural.evidence.requestedProvider === 'wasm' &&
      neural.evidence.gpuExecutionVerified === false,
    neural.evidence,
  );
  report.measurements.neuralOnlineMs = neural.ms;

  // Warm offline: the runtime and inference session are initialized and the
  // positions are already prepared. No server request may be required.
  const offlineRequestsBefore = pageRequests.length;
  await context.setOffline(true);
  const offlineJarl = await run(jarlData, true);
  const offlineNeural = await run(neuralData, true);
  await context.setOffline(false);
  check('warm offline Jarl completes locally', offlineJarl.report.status === 'complete');
  check(
    'warm offline neural MCTS completes locally with the same model',
    offlineNeural.report.evaluations[0]?.source === 'neural_search' &&
      offlineNeural.model?.sha256 === published.onnx_sha256,
  );
  const offlineAnalysisRequests = pageRequests
    .slice(offlineRequestsBefore)
    .filter(
      (url) =>
        url.includes('/api/browser-analysis/') ||
        url.includes('/analysis-context') ||
        /\/api\/games\/[^/]+\/analysis$/.test(url),
    );
  check(
    'no analysis or authorization request was needed during warm offline searches',
    offlineAnalysisRequests.length === 0,
    offlineAnalysisRequests,
  );
  report.measurements.offlineJarlMs = offlineJarl.ms;
  report.measurements.offlineNeuralMs = offlineNeural.ms;

  // Exercise the production React integration: the existing game page must run
  // local analysis transparently, with no POST to the legacy analysis endpoint.
  const uiRequestsBefore = pageRequestDetails.length;
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`${baseUrl}/games/${game.id}`, { waitUntil: 'load' });
  const openAnalytics = async () => {
    await page.waitForSelector('.agent-analysis', { state: 'attached', timeout: 90_000 });
    const tabs = page.getByRole('tab', { name: 'Analytics' });
    for (let index = 0; index < (await tabs.count()); index += 1) {
      const candidate = tabs.nth(index);
      if (await candidate.isVisible()) {
        await candidate.click();
        break;
      }
    }
    await page.waitForSelector('.agent-estimate', { timeout: 90_000 });
  };
  await openAnalytics();
  const deviceText =
    (await page
      .getByText(/Analysis (runs|completed) on this device/)
      .first()
      .textContent()) ?? '';
  check(
    'existing analysis UI indicates local execution',
    /this device|diesem Gerät/i.test(deviceText),
    deviceText,
  );
  check(
    'the existing analysis UI renders an advisory evaluation',
    (await page.locator('.agent-estimate').count()) > 0,
  );
  const uiRequests = pageRequestDetails.slice(uiRequestsBefore);
  check(
    'the production UI never posts to the server analysis endpoint',
    !uiRequests.some(
      (request) => request.method === 'POST' && request.url.includes(`/api/games/${game.id}/analysis`),
    ),
    uiRequests.filter((request) => request.method === 'POST'),
  );

  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    const themes = ['kings-table', 'iron-ash', 'saga-hall'];
    for (const theme of themes) {
      await page.evaluate(
        ({ theme }) =>
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme, tokens: 'wood', board: 'walnut' }),
          ),
        { theme },
      );
      await page.reload({ waitUntil: 'load' });
      await openAnalytics();
      await page.screenshot({ path: `${screenshots}/analysis-${theme}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'load' });
    await openAnalytics();
    await page.screenshot({ path: `${screenshots}/analysis-mobile.png`, fullPage: true });
  }

  const metricsAfter = await context.request.get(`${baseUrl}/api/analysis/metrics`, { headers });
  const after = await metricsAfter.json();
  check(
    'successful local analysis caused zero server analysis computation',
    (after.counters.admitted ?? 0) === (before.counters.admitted ?? 0) &&
      (after.counters.fallback_request ?? 0) === (before.counters.fallback_request ?? 0),
    { before: before.counters, after: after.counters },
  );

  report.requests = {
    runtimeAssets: pageRequests.filter((url) => url.includes('/browser-analysis/runtime/')).length,
    modelArtifacts: pageRequests.filter((url) => url.includes('/artifact')).length,
  };
  report.game = { id: game.id, publication: published.publication_id, model: published.onnx_sha256 };
  report.results = { jarl, neural, offlineJarl, offlineNeural };
  report.ordinaryPageRequests = pageRequests.length;
} finally {
  await browser.close();
}
report.ok = results.every(Boolean);
if (output) await writeFile(output, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ ok: report.ok, checks: report.checks, requests: report.requests, measurements: report.measurements }, null, 2));
process.exit(report.ok ? 0 : 1);
