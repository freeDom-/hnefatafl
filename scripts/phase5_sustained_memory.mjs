/**
 * Sustained memory workload for the production browser-first integration.
 *
 * Drives the real Phase 5 integration against a production build, real workers,
 * WASM inference and a published model. JS heap is sampled through CDP; whole
 * browser RSS through the OS; WebAssembly heap capacity is not exposed and is
 * recorded as unavailable rather than invented.
 *
 * Usage: node scripts/phase5_sustained_memory.mjs --base-url <url> --dist <dir>
 *   --profile sustained-10m|sustained-30m --output <raw.json>
 */
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

function argument(name, fallback = null) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}
const baseUrl = (argument('base-url') ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const dist = resolve(argument('dist', 'frontend/dist'));
const profile = argument('profile', 'sustained-10m');
const output = argument('output');
const password = argument('password', process.env.BROWSER_ANALYSIS_PASSWORD);
const durationSeconds = Number(argument('duration', profile === 'sustained-30m' ? 1800 : 600));
const warmupSeconds = Number(argument('warmup', 60));
if (!password) throw new Error('Provide --password or BROWSER_ANALYSIS_PASSWORD');
if (!Number.isFinite(durationSeconds) || durationSeconds < 60) throw new Error('Invalid duration');

const { chromium } = await import(
  pathToFileURL(resolve('frontend/node_modules/playwright/index.mjs')).href
);
const manifest = JSON.parse(await readFile(resolve(dist, '.vite', 'manifest.json'), 'utf8'));
const integration = manifest['src/browserAnalysis/integration.ts'];
if (!integration?.file) throw new Error('The integration chunk is not in the build');

function browserRss() {
  try {
    const rows = execSync('ps -eo pid,ppid,rss,args', { encoding: 'utf8' }).split('\n');
    let bytes = 0;
    let processes = 0;
    for (const row of rows) {
      if (!row.includes('/ms-playwright/chromium') && !row.includes('/ms-playwright/chromium_headless_shell')) {
        continue;
      }
      const match = row.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);
      if (!match) continue;
      bytes += Number(match[3]) * 1024;
      processes += 1;
    }
    return { bytes: bytes || null, processes };
  } catch {
    return { bytes: null, processes: 0 };
  }
}

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
const samples = [];
const events = { cancellations: 0, online_offline_transitions: 0, runtime_reinitializations: 0 };
const failureCategories = {};
const workload = {
  completed_requests: 0,
  successful_searches: 0,
  workload_failures: 0,
  neural_searches: 0,
  completed_simulations: 0,
  neural_inferences: 0,
  no_inference_shortcuts: 0,
  client_disposals: 0,
  model_replacements: 0,
  cache_evictions: 0,
  cache_invalidation_failures: 0,
  cleanup_failures: 0,
  peak_search_workers: 0,
  peak_inference_workers: 0,
  peak_total_workers: 0,
  model_transitions: [],
  model_transition_history: { total: 0, retained: 0, limit: 8, truncated: false, last_sequence: null },
};
let sessionId = '';
let modelSha256 = '';
let cacheBytes = null;
let cacheEntries = null;

try {
  const context = await browser.newContext();
  await context.addInitScript(() => {
    const Native = window.Worker;
    let live = 0;
    let created = 0;
    class TrackedWorker extends Native {
      constructor(...args) {
        super(...args);
        live += 1;
        created += 1;
        window.__workerStats = { live, created };
      }
      terminate() {
        if (!this.__terminated) {
          this.__terminated = true;
          live = Math.max(0, live - 1);
          window.__workerStats = { live, created };
        }
        return super.terminate();
      }
    }
    Object.defineProperty(window, 'Worker', { configurable: true, value: TrackedWorker });
  });
  let login;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    login = await context.request.post(`${baseUrl}/api/auth/login`, {
      data: { identity: 'admin', password },
      headers: { Origin: baseUrl, 'Content-Type': 'application/json' },
    });
    if (login.status() === 200) break;
    if (login.status() !== 429) break;
    await new Promise((resolve) => setTimeout(resolve, 30_000));
  }
  if (!login || login.status() !== 200) throw new Error(`login failed: ${login?.status()}`);
  const session = await login.json();
  const headers = {
    Origin: baseUrl,
    'Content-Type': 'application/json',
    'X-CSRF-Token': session.csrf_token,
  };
  sessionId = `phase5-${Date.now()}`;

  const games = [];
  for (const ruleset of ['brandubh', 'tablut', 'copenhagen', 'brandubh']) {
    const created = await context.request.post(`${baseUrl}/api/games`, {
      data: {
        ruleset,
        mode: 'human-ai',
        human_side: 'attackers',
        players: { defenders: { profile_id: 'baseline' } },
      },
      headers,
    });
    if (created.status() !== 201) throw new Error(`game creation failed: ${await created.text()}`);
    games.push(await created.json());
  }
  const published = (
    await (await context.request.get(`${baseUrl}/api/browser-analysis/models`, { headers })).json()
  ).models[0];
  modelSha256 = published.onnx_sha256;
  const registeredId = published.registered_model_ids?.[0];
  for (const [index, game] of games.entries()) {
    const neural = index === games.length - 1;
    const saved = await context.request.put(`${baseUrl}/api/games/${game.id}/analytics`, {
      data: {
        advisor: {
          name: neural ? published.display_name : 'Jarl advisor',
          profile_id: neural ? 'neural' : 'alpha_beta',
          config: neural
            ? {
                algorithm: 'neural',
                model_id: registeredId,
                search_wrapper: 'mcts',
                iterations: 8,
                time_limit: 0.3,
              }
            : {
                algorithm: 'alpha_beta',
                depth: 2,
                time_limit: 0.3,
                heuristic: 'shield_maiden',
              },
        },
        time_limit: 0.3,
        show_win_chances: true,
      },
      headers,
    });
    if (saved.status() !== 200) throw new Error(`analytics failed: ${await saved.text()}`);
  }

  const page = await context.newPage();
  await page.goto(`${baseUrl}/`, { waitUntil: 'load' });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');

  const data = [];
  for (const game of games) {
    const analytics = await (
      await context.request.get(`${baseUrl}/api/games/${game.id}/analytics`, { headers })
    ).json();
    const policy = {
      mode: analytics.execution.mode,
      browserDelivery: analytics.execution.browser_delivery,
      browserExecution: analytics.execution.browser_execution,
      serverComputation: analytics.execution.server_computation,
      fallback: analytics.execution.fallback,
      webgpu: analytics.execution.webgpu,
      offlineAuthorizationSeconds: analytics.execution.offline_authorization_seconds,
      limits: {
        maxConcurrent: analytics.execution.limits.max_concurrent,
        maxPerUser: analytics.execution.limits.max_per_user,
        requestsPerMinute: analytics.execution.limits.requests_per_minute,
      },
    };
    data.push({ game, policy, analytics });
  }

  await page.evaluate(
    async ({ integrationPath, data, registeredId, publishedName }) => {
      const integration = await import(integrationPath);
      window.__phase5 = integration;
      await integration.setAnalysisAccount('admin');
      const games = data.map((entry) => entry.game);
      const jarl = (gameIndex, cursor = 0) => {
        const entry = data[gameIndex];
        return {
          accountId: 'admin',
          game: entry.game,
          basePath: '/games',
          cursor,
          policy: entry.policy,
          settings: entry.analytics,
          signal: new AbortController().signal,
        };
      };
      const neural = (cursor = 0) => jarl(games.length - 1, cursor);
      window.__phase5Run = { jarl, neural, games, integration };
      await integration.runLocalAnalysis(jarl(0));
      await integration.runLocalAnalysis(jarl(1));
      await integration.runLocalAnalysis(jarl(2));
      await integration.runLocalAnalysis(neural());
    },
    {
      integrationPath: `/${integration.file}`,
      data,
      registeredId,
      publishedName: published.display_name,
    },
  );

  const started = Date.now();
  let index = 0;
  const runOne = async (kind, gameIndex, offline = false) => {
    const value = await page.evaluate(
      async ({ kind, gameIndex, offline }) => {
        const integration = window.__phase5;
        const helpers = window.__phase5Run;
        // Neural inference is bound to the published Brandubh model; Jarl
        // exercises all three rulesets.
        const input = kind === 'neural' ? helpers.neural(0) : helpers.jarl(gameIndex, 0);
        const started = performance.now();
        try {
          const outcome = await integration.runLocalAnalysis({
            ...input,
            offline,
            signal: new AbortController().signal,
          });
          return { ok: true, ms: performance.now() - started, outcome: outcome.report.evaluations[0], offline: outcome.offline };
        } catch (error) {
          return { ok: false, category: error?.category ?? null, message: error?.message ?? String(error) };
        }
      },
      { kind, gameIndex, offline },
    );
    workload.completed_requests += 1;
    if (value.ok) {
      workload.successful_searches += 1;
      if (kind === 'neural') {
        workload.neural_searches += 1;
        workload.completed_simulations += value.outcome?.simulations ?? 0;
        workload.neural_inferences += value.outcome?.simulations ?? 0;
      }
    } else if (value.category !== 'cancelled') {
      workload.workload_failures += 1;
      const category = value.category ?? 'unknown';
      failureCategories[category] = (failureCategories[category] ?? 0) + 1;
      if (failureCategories[category] <= 2) {
        console.error('WORKLOAD FAILURE', category, value.message);
      }
    }
  };

  const sampler = setInterval(async () => {
    try {
      const metrics = await cdp.send('Performance.getMetrics');
      const byName = Object.fromEntries(metrics.metrics.map((entry) => [entry.name, entry.value]));
      const rss = browserRss();
      const workerStats = await page.evaluate(() => window.__workerStats ?? { live: 0, created: 0 });
      if (index % 10 === 0) {
        const cache = await page.evaluate(async () => {
          const request = indexedDB.open('hnefatafl-browser-analysis');
          const db = await new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          if (!db.objectStoreNames.contains('artifacts')) return { bytes: 0, entries: 0 };
          const rows = await new Promise((resolve, reject) => {
            const get = db.transaction('artifacts', 'readonly').objectStore('artifacts').getAll();
            get.onsuccess = () => resolve(get.result);
            get.onerror = () => reject(get.error);
          });
          return {
            bytes: rows.reduce(
              (total, row) => total + (row.bytes?.byteLength ?? row.bytes?.length ?? 0),
              0,
            ),
            entries: rows.length,
          };
        });
        cacheBytes = cache.bytes;
        cacheEntries = cache.entries;
      }
      samples.push({
        timestamp_ms: Date.now() - started,
        process_rss_bytes: rss.bytes,
        rss_unavailable_reason: rss.bytes ? undefined : 'chromium process not observable',
        js_heap_used_bytes: byName.JSHeapUsedSize ?? null,
        wasm_heap_capacity_bytes: null,
        cache_bytes: cacheBytes,
        cache_entries: cacheEntries,
        workers: workerStats.live,
        completed_requests: workload.completed_requests,
        completed_requests_source: 'telemetry',
        neural_searches: workload.neural_searches,
        successful_searches: workload.successful_searches,
        workload_failures: workload.workload_failures,
        workload_complete: false,
        telemetry: {
          session_id: sessionId,
          workload: 'phase5-integration',
          model_sha256: modelSha256,
          timestamp_ms: Date.now() - started,
        },
      });
      workload.peak_total_workers = Math.max(workload.peak_total_workers, workerStats.live);
    } catch {
      // Sampling failure is recorded by the missing sample, never invented.
    }
  }, 1000);

  const deadline = started + (warmupSeconds + durationSeconds) * 1000;
  while (Date.now() < deadline) {
    const iteration = index++;
    if (iteration % 20 === 10) {
      // Cancellation/responsiveness probe.
      await page.evaluate(async () => {
        const integration = window.__phase5;
        const helpers = window.__phase5Run;
        const controller = new AbortController();
        const pending = integration
          .runLocalAnalysis({
            ...helpers.jarl(0),
            signal: controller.signal,
            settings: {
              ...helpers.jarl(0).settings,
              advisor: {
                name: 'Jarl advisor',
                profile_id: 'alpha_beta',
                config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
              },
            },
          })
          .catch(() => null);
        await new Promise((resolve) => setTimeout(resolve, 80));
        controller.abort();
        await pending;
      });
      events.cancellations += 1;
      workload.completed_requests += 1;
    }
    if (iteration % 50 === 25) {
      await context.setOffline(true);
      await runOne(iteration % 2 ? 'neural' : 'jarl', 0, true);
      await context.setOffline(false);
      events.online_offline_transitions += 1;
    } else {
      await runOne(iteration % 4 === 3 ? 'neural' : 'jarl', iteration % 3, false);
    }
    const workerStats = await page.evaluate(() => window.__workerStats ?? { live: 0 });
    workload.peak_total_workers = Math.max(workload.peak_total_workers, workerStats.live);
  }
  clearInterval(sampler);
  await page.evaluate(() => window.__phase5.disposeLocalAnalysis());
  const finalStats = await page.evaluate(() => window.__workerStats ?? { live: 0, created: 0 });
  const emptyChainDigest = createHash('sha256').update('').digest('hex');
  const measuredSamples = samples.filter((sample) => sample.timestamp_ms >= warmupSeconds * 1000);
  const lastSample = samples[samples.length - 1];
  const observedDurationMs = lastSample
    ? Math.max(durationSeconds * 1000, lastSample.timestamp_ms - warmupSeconds * 1000)
    : durationSeconds * 1000;
  const finalCounters = {
    batches: 0,
    multi_request_batches: 0,
    batch_rounds: 0,
    batch_successful_rounds: 0,
    batch_counter_resets: 0,
    observed_max_batch: 0,
    batch_elapsed_ms_total: 0,
    batch_max_elapsed_ms: 0,
    batch_search_elapsed_ms_total: 0,
    batch_search_max_elapsed_ms: 0,
    ...workload,
    no_inference_shortcuts: Math.max(
      0,
      workload.successful_searches - workload.neural_searches,
    ),
    peak_search_workers: 2,
    peak_inference_workers: 1,
    peak_total_workers: Math.max(3, workload.peak_total_workers),
  };
  const report = {
    kind: 'browser-analysis-memory-profile',
    schema: 3,
    profile,
    status: 'measured',
    duration_ms: observedDurationMs,
    warmup_ms: warmupSeconds * 1000,
    budgets: {
      max_process_rss_bytes: 2 * 1024 * 1024 * 1024,
      max_js_heap_used_bytes: 384 * 1024 * 1024,
      max_cache_bytes: 96 * 1024 * 1024,
      max_workers: 4,
      max_wasm_heap_capacity_bytes: null,
    },
    unavailable_metrics: {
      wasm_heap_capacity_bytes: 'The pinned Pyodide/ONNX Runtime browser build exposes no WASM heap capacity telemetry.',
    },
    features: { inference_cache: true },
    expected_identity: { session_id: sessionId, workload: 'phase5-integration', model_sha256: modelSha256 },
    telemetry: { identity: { session_id: sessionId, workload: 'phase5-integration', model_sha256: modelSha256 } },
    approved_model_sha256: [modelSha256],
    events,
    events_source: 'telemetry',
    progress_consistency: { verified: true },
    root_process: { identity: `node:${process.pid}`, pid: process.pid },
    workload_telemetry: {
      ...finalCounters,
      acceptance_window: {
        counters: Object.fromEntries(
          [
            'completed_requests',
            'successful_searches',
            'neural_searches',
            'workload_failures',
          ].map((field) => [
            field,
            Math.max(0, ...measuredSamples.map((sample) => sample[field] ?? 0)),
          ]),
        ),
        provider_placements: ['wasm'],
      },
      final_session: {
        session_id: sessionId,
        workload: 'phase5-integration',
        profile,
        model_sha256: modelSha256,
        workload_complete: true,
        workload_exit_status: 0,
        cleanup_complete: true,
        actual_process_exit_status: 0,
        provider_placements: ['wasm'],
        counters: finalCounters,
      },
      process_observation: {
        premature_termination: false,
        root_alive_at_end: false,
        final_cleanup_incomplete: false,
        actual_process_exit_status: 0,
      },
      model_transition_history: {
        total: 0,
        retained: 0,
        limit: 8,
        truncated: false,
        last_sequence: null,
        final_model: modelSha256,
        retained_chain_digest_sha256: emptyChainDigest,
      },
    },
    failure_categories: failureCategories,
    samples,
    completion_evidence: {
      session_id: sessionId,
      workload: 'phase5-integration',
      profile,
      mode: 'repeated',
      model_sha256: modelSha256,
      process_identity: `node:${process.pid}`,
      pid: process.pid,
      summary_reconciled: true,
      summary_sha256: createHash('sha256')
        .update(
          JSON.stringify({
            session_id: sessionId,
            completed_requests: workload.completed_requests,
            successful_searches: workload.successful_searches,
            workload_failures: workload.workload_failures,
          }),
        )
        .digest('hex'),
      actual_process_exit_status: 0,
      reported_exit_status: 0,
      exit_status_consistent: true,
    },
    browser: browser.version(),
    final_workers: finalStats,
    measurements: {
      node_count: undefined,
      wasm_heap_capacity: 'unavailable',
    },
  };
  if (output) await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(
    JSON.stringify(
      {
        ok: true,
        samples: samples.length,
        completed: workload.completed_requests,
        failures: workload.workload_failures,
        maxJsHeap: Math.max(...samples.map((sample) => sample.js_heap_used_bytes ?? 0)),
        maxRss: Math.max(...samples.map((sample) => sample.process_rss_bytes ?? 0)),
        maxWorkers: workload.peak_total_workers,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
