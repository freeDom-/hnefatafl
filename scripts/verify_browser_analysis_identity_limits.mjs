/**
 * Production-browser identity/time-limit probe for the Phase 5 integration.
 *
 * Exercises a non-integer server-resolved analytics time limit through the real
 * production bundle, backend, CSP, workers and WASM so the canonical identity
 * contract is tested against actual engine responses.
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
const output = argument('output');
const password = argument('password', process.env.BROWSER_ANALYSIS_PASSWORD);
if (!password) throw new Error('Provide --password or BROWSER_ANALYSIS_PASSWORD');

const { chromium } = await import(
  pathToFileURL(resolve('frontend/node_modules/playwright/index.mjs')).href
);
const manifest = JSON.parse(await readFile(resolve(dist, '.vite', 'manifest.json'), 'utf8'));
const integration = manifest['src/browserAnalysis/integration.ts'];
if (!integration?.file) throw new Error('The integration chunk is not in the build');

const checks = [];
function check(name, ok, detail = null) {
  checks.push({ name, ok: Boolean(ok), detail });
  if (!ok) throw new Error(`check failed: ${name}${detail ? ` (${JSON.stringify(detail)})` : ''}`);
}

const browser = await chromium.launch();
let resultReport = null;
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
  const game = await created.json();
  const saved = await context.request.put(`${baseUrl}/api/games/${game.id}/analytics`, {
    data: {
      advisor: {
        name: 'Jarl advisor',
        profile_id: 'alpha_beta',
        config: { algorithm: 'alpha_beta', depth: 3, time_limit: 1, heuristic: 'shield_maiden' },
      },
      time_limit: 2.5,
      show_win_chances: true,
    },
    headers,
  });
  check('non-integer analytics time limit is saved', saved.status() === 200, await saved.text());
  const settings = await saved.json();
  check('server resolves the non-integer effective limit', settings.time_limit === 2.5);
  const current = await context.request.get(`${baseUrl}/api/games/${game.id}`, { headers });
  check('game is readable', current.status() === 200);
  const currentGame = await current.json();

  const page = await context.newPage();
  await page.goto(`${baseUrl}/`, { waitUntil: 'load' });
  const result = await page.evaluate(
    async ({ integrationPath, gameData, settingsData }) => {
      const module = await import(integrationPath);
      await module.setAnalysisAccount('account');
      const policy = module.normalizeExecutionPolicy(settingsData.execution);
      const controller = new AbortController();
      return module.runLocalAnalysis({
        accountId: 'account',
        game: gameData,
        basePath: '/games',
        cursor: gameData.cursor,
        settings: settingsData,
        policy,
        signal: controller.signal,
        offline: false,
      });
    },
    {
      integrationPath: `/${integration.file}`,
      gameData: currentGame,
      settingsData: settings,
    },
  );
  check('non-integer Jarl search completes', result.report.status === 'complete');
  check('effective time limit is preserved', result.report.evaluations[0]?.config?.time_limit === 2.5);
  check('search reports the analyzed seed', Number.isInteger(result.report.evaluations[0]?.search_seed));
  check('canonical position key is present', typeof result.report.position_key === 'string');
  resultReport = result.report;
} finally {
  await browser.close();
}

const payload = {
  schema: 1,
  baseUrl,
  checks,
  report: resultReport,
  ok: checks.every((item) => item.ok),
};
if (output) await writeFile(output, JSON.stringify(payload, null, 2) + '\n');
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.ok ? 0 : 1);
