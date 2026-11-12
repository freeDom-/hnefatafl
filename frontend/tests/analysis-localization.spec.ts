import {
  openDestination,
  openGameNavigation,
  openProfile,
  returnToGame,
  openGameOptions,
  gameTab,
  showMove,
} from './play-helpers';
import { fixtureModel, historicalGame } from './historical-fixture';
import { expect, test } from './auth-fixture';
import type { APIRequestContext, Page } from './auth-fixture';
import type { AnalyticsSettings } from '../src/analysisTypes';
import type { Model } from '../src/types';

async function editingNeuralAdvisor(page: Page, request: APIRequestContext, custom = false) {
  const model: Model = await (
    await fixtureModel(request, { data: { name: 'Draft switching raven' } })
  ).json();
  const game = await (
    await historicalGame(request, { data: { ruleset: 'brandubh', mode: 'human-human' } })
  ).json();
  const path = `http://127.0.0.1:8001/api/games/${game.id}/analytics`;
  const selected = await request.put(path, {
    data: {
      advisor: {
        name: custom ? 'Custom move advisor' : model.name,
        profile_id: custom ? undefined : 'neural',
        config: { algorithm: 'neural', model_id: model.id, iterations: 1024 },
      },
      time_limit: 0.05,
      show_win_chances: true,
    },
  });
  expect(selected.ok()).toBe(true);
  const initial: AnalyticsSettings = await selected.json();
  const writes: AnalyticsSettings[] = [];
  page.on('request', (outgoing) => {
    if (outgoing.method() === 'PUT' && outgoing.url().endsWith(`/games/${game.id}/analytics`))
      writes.push(outgoing.postDataJSON());
  });
  // Keep drafts pending regardless of runner speed, then explicitly advance
  // beyond the debounce to catch callbacks left behind by a focus/owner change.
  const frozen = new Date();
  await page.clock.install({ time: frozen });
  await page.clock.pauseAt(new Date(frozen.getTime() + 1000));
  await page.goto(`/games/${game.id}`);
  await openGameOptions(page);
  const panel = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = panel.getByRole('combobox', { name: 'Move advisor', exact: true });
  const iterations = panel.getByRole('spinbutton', { name: 'MCTS Iterations', exact: true });
  await expect(iterations).toHaveValue('1024');
  const read = async (): Promise<AnalyticsSettings> => (await request.get(path)).json();
  const settled = async (count: number) => {
    await page.clock.fastForward(1200);
    await expect(panel.getByText('Saving analytics settings…')).toHaveCount(0);
    await expect.poll(async () => (await read()).revision).toBe(initial.revision + count);
    const saved = await read();
    expect(writes).toHaveLength(count);
    expect(saved.revision).toBe(initial.revision + count);
    return saved;
  };
  return { model, panel, advisor, iterations, path, writes, settled, read };
}

test('clicking another advisor discards the previous neural MCTS draft before blur can save it', async ({
  page,
  request,
}) => {
  const { advisor, iterations, writes, settled } = await editingNeuralAdvisor(page, request);
  await iterations.fill('4096');
  // selectOption alone does not move focus and misses the native blur ordering.
  await advisor.click();
  await advisor.selectOption('profile:baseline');
  const saved = await settled(1);
  expect(writes[0].advisor?.profile_id).toBe('baseline');
  expect(saved.advisor?.profile_id).toBe('baseline');
  await expect(advisor).toHaveValue('profile:baseline');
});

test('keyboard advisor selection cancels the draft and its timer while the selector has focus', async ({
  page,
  request,
}) => {
  const { advisor, iterations, writes, settled } = await editingNeuralAdvisor(page, request);
  await iterations.fill('2048');
  // Transfer real DOM focus, then use the native select's keyboard handling.
  await advisor.focus();
  await page.clock.fastForward(1200);
  expect(writes).toEqual([]);
  await advisor.press('Home');
  const saved = await settled(1);
  expect(writes[0].advisor).toBeNull();
  expect(saved.advisor).toBeNull();
  await expect(advisor).toHaveValue('');
});

test('clicking search time commits the valid MCTS draft once and preserves it in the next settings write', async ({
  page,
  request,
}) => {
  const { model, panel, iterations, writes, settled } = await editingNeuralAdvisor(page, request);
  await iterations.fill('4096');
  const searchTime = panel.getByRole('spinbutton', { name: 'Maximum search time (seconds)' });
  await searchTime.click();
  await settled(1);
  await searchTime.fill('0.1');
  const saved = await settled(2);
  expect(writes.map((value) => value.advisor?.config.iterations)).toEqual([4096, 4096]);
  expect(saved).toMatchObject({
    advisor: { config: { model_id: model.id, iterations: 4096 } },
    time_limit: 0.1,
  });
});

test('leaving analytics after an ordinary blur commits once and leaves no delayed draft writes', async ({
  page,
  request,
}) => {
  const { model, iterations, settled, read, writes } = await editingNeuralAdvisor(page, request);
  await iterations.fill('2048');
  await openGameNavigation(page);
  await expect(page).toHaveURL(/\/dashboard$/);
  const saved = await settled(1);
  expect(saved.advisor?.config).toMatchObject({ model_id: model.id, iterations: 2048 });
  await page.clock.fastForward(5000);
  expect(writes).toHaveLength(1);
  expect(await read()).toEqual(saved);
});

test('switching neural models saves only the new advisor with its default MCTS budget', async ({
  page,
  request,
}) => {
  const next: Model = await (
    await fixtureModel(request, { data: { name: 'Next draft switching raven' } })
  ).json();
  const { advisor, iterations, writes, settled } = await editingNeuralAdvisor(page, request);
  await iterations.fill('4096');
  await advisor.click();
  await advisor.selectOption(`model:${next.id}`);
  const saved = await settled(1);
  expect(writes[0].advisor?.config).toMatchObject({ model_id: next.id, iterations: 1024 });
  expect(saved.advisor?.config).toMatchObject({ model_id: next.id, iterations: 1024 });
  await expect(iterations).toHaveValue('1024');
});

test('tabbing to a custom neural algorithm discards the old MCTS draft before keyboard selection', async ({
  page,
  request,
}) => {
  const { panel, iterations, writes, settled } = await editingNeuralAdvisor(page, request, true);
  await iterations.fill('4096');
  const algorithm = panel.getByRole('combobox', { name: 'Algorithm', exact: true });
  await iterations.press('Tab');
  await expect(algorithm).toBeFocused();
  await algorithm.press('Home');
  const saved = await settled(1);
  expect(writes[0].advisor?.config).toMatchObject({ algorithm: 'baseline', iterations: 1024 });
  expect(saved.advisor?.config.algorithm).toBe('baseline');
  await expect(iterations).toHaveCount(0);
});

test('changing a custom neural model discards the previous model draft', async ({
  page,
  request,
}) => {
  const next: Model = await (
    await fixtureModel(request, { data: { name: 'Next custom switching raven' } })
  ).json();
  const { panel, iterations, writes, settled } = await editingNeuralAdvisor(page, request, true);
  await iterations.fill('2048');
  const model = panel.getByRole('combobox', { name: 'Trained model', exact: true });
  await model.click();
  await model.selectOption(next.id);
  const saved = await settled(1);
  expect(writes[0].advisor?.config).toMatchObject({ model_id: next.id, iterations: 1024 });
  expect(saved.advisor?.config).toMatchObject({ model_id: next.id, iterations: 1024 });
  await expect(iterations).toHaveValue('1024');
});

test('Enter commits once before switching advisors', async ({ page, request }) => {
  const { advisor, iterations, model, writes, settled } = await editingNeuralAdvisor(page, request);
  await iterations.fill('4096');
  await iterations.press('Enter');
  await settled(1);
  await advisor.click();
  await advisor.selectOption('profile:baseline');
  const saved = await settled(2);
  expect(writes[0].advisor?.config).toMatchObject({ model_id: model.id, iterations: 4096 });
  expect(writes[1].advisor?.profile_id).toBe('baseline');
  expect(saved.advisor?.profile_id).toBe('baseline');
});

test('rapid typing and advisor switches cannot let an older response restore the old configuration', async ({
  page,
  request,
}) => {
  const next: Model = await (
    await fixtureModel(request, { data: { name: 'Rapid switching raven' } })
  ).json();
  const { advisor, iterations, path, writes, settled } = await editingNeuralAdvisor(page, request);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(path.replace('http://127.0.0.1:8001', '**'), async (route) => {
    if (route.request().method() !== 'PUT') return route.continue();
    const response = await route.fetch();
    await delayed;
    await route.fulfill({ response });
  });
  try {
    await iterations.fill('');
    await iterations.pressSequentially('4096');
    await advisor.click();
    await advisor.selectOption(`model:${next.id}`);
    await expect.poll(() => writes.length).toBe(1);
    await advisor.click();
    await advisor.selectOption('profile:baseline');
    await expect(advisor).toHaveValue('profile:baseline');
    release();
    const saved = await settled(2);
    expect(writes[0].advisor?.config).toMatchObject({ model_id: next.id, iterations: 1024 });
    expect(writes[1].advisor?.profile_id).toBe('baseline');
    expect(saved.advisor?.profile_id).toBe('baseline');
    await expect(advisor).toHaveValue('profile:baseline');
  } finally {
    release();
  }
});

test('neural analytics budgets persist across search windows, replay and reload on desktop and mobile', async ({
  page,
  request,
}) => {
  const model = await (await fixtureModel(request, { data: { name: 'Analytics Hugin' } })).json();
  const stored = await historicalGame(request, {
    data: { ruleset: 'brandubh', mode: 'human-human' },
  });
  const game = await stored.json();
  await page.goto(`/games/${game.id}`);
  await openGameOptions(page);
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = analytics.getByRole('combobox', { name: 'Move advisor', exact: true });
  const iterations = analytics.getByRole('spinbutton', { name: 'MCTS Iterations', exact: true });
  const searchTime = analytics.getByRole('spinbutton', { name: 'Maximum search time (seconds)' });
  await expect(iterations).toHaveCount(0);
  // Keep real neural searches short; this exercises the full pipeline without
  // asking routine browser CI to complete thousands of neural simulations.
  await searchTime.fill('0.05');
  await advisor.selectOption(`model:${model.id}`);
  await expect(iterations).toHaveValue('1024');
  await expect
    .poll(
      async () =>
        (await (await request.get(`http://127.0.0.1:8001/api/games/${game.id}/analytics`)).json())
          .advisor?.config.iterations,
    )
    .toBe(1024);
  const writes: number[] = [];
  let editing = true;
  page.on('request', (outgoing) => {
    if (
      editing &&
      outgoing.method() === 'PUT' &&
      new URL(outgoing.url()).pathname === `/api/games/${game.id}/analytics`
    )
      writes.push(outgoing.postDataJSON().advisor.config.iterations);
  });
  await iterations.fill('');
  await iterations.pressSequentially('4096', { delay: 50 });
  await expect
    .poll(
      async () =>
        (await (await request.get(`http://127.0.0.1:8001/api/games/${game.id}/analytics`)).json())
          .advisor?.config.iterations,
    )
    .toBe(4096);
  expect(writes).toEqual([4096]);
  editing = false;
  await searchTime.fill('0.1');
  await expect
    .poll(
      async () =>
        (await (await request.get(`http://127.0.0.1:8001/api/games/${game.id}/analytics`)).json())
          .time_limit,
    )
    .toBe(0.1);
  await expect(iterations).toHaveValue('4096');
  await analytics.getByRole('button', { name: 'Explain MCTS Iterations', exact: true }).click();
  await expect(page.getByRole('tooltip')).toContainText('per analyzed position');
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  await gameTab(page, 'analytics');
  const estimates = page.getByRole('region', { name: 'Agent evaluations' });
  await expect(estimates).toHaveAttribute('aria-busy', 'false');
  const response = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/analysis`, {
    data: { version: game.version, cursor: game.cursor, background: true },
  });
  expect(response.ok(), await response.text()).toBe(true);
  const initial = await response.json();
  expect(initial.evaluations[0].config.iterations).toBe(4096);
  const board = page.getByRole('grid', { name: 'brandubh game board' });
  await board
    .getByRole('gridcell', { name: /attacker/ })
    .first()
    .click();
  await board
    .getByRole('gridcell', { name: /empty, legal move/ })
    .first()
    .click();
  await showMove(page, 1);
  await page.getByRole('button', { name: '↤ Starting position', exact: true }).click();
  await page.reload();
  await openGameOptions(page);
  await expect(iterations).toHaveValue('4096');
  await advisor.selectOption('profile:baseline');
  await expect(iterations).toHaveCount(0);
});

test('temporary analysis capacity errors recover automatically on desktop and mobile', async ({
  page,
  request,
}) => {
  const stored = await historicalGame(request, {
    data: { ruleset: 'brandubh', mode: 'human-human' },
  });
  const game = await stored.json();
  let attempts = 0;
  await page.route(`**/api/games/${game.id}/analysis`, async (route) => {
    if (route.request().method() === 'POST' && ++attempts === 1)
      await route.fulfill({
        status: 503,
        headers: { 'Content-Type': 'application/json', 'Retry-After': '2' },
        body: JSON.stringify({ detail: 'Analysis capacity is busy. Retry later.' }),
      });
    else await route.continue();
  });
  await page.goto(`/games/${game.id}`);
  await openGameOptions(page);
  await page
    .getByRole('region', { name: 'Analytics', exact: true })
    .getByRole('combobox', { name: 'Move advisor', exact: true })
    .selectOption('profile:baseline');
  await gameTab(page, 'analytics');
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  await expect(panel.getByRole('status')).toHaveText('Waiting for analysis capacity…');
  await expect(panel).toHaveAttribute('aria-busy', 'true');
  await expect(panel.getByRole('button', { name: /Retry/ })).toHaveCount(0);
  await expect(panel.getByRole('meter')).toHaveCount(1);
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test('a searching advisor publishes partial results while the human can move and can be changed immediately', async ({
  page,
  request,
}) => {
  const stored = await historicalGame(request, {
    data: { ruleset: 'copenhagen', mode: 'human-human' },
  });
  expect(stored.ok()).toBe(true);
  await page.goto('/games/' + (await stored.json()).id);
  await openGameOptions(page);
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = analytics.getByRole('combobox', { name: 'Move advisor', exact: true });
  // A long search leaves time to return from settings to the fixed mobile panel
  // and verify that board input remains available during unfinished analysis.
  await analytics.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }).fill('15');
  await advisor.selectOption('custom');
  await expect(
    analytics.getByRole('spinbutton', { name: 'Search depth', exact: true }),
  ).toHaveValue('5');
  await gameTab(page, 'analytics');
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  const advisorOpinion = panel
    .locator('.agent-estimate')
    .filter({ has: page.getByText('Move advisor', { exact: true }) });
  await expect(panel).toHaveAttribute('aria-busy', 'true');
  await expect(advisorOpinion.locator('.estimate-source')).toContainText(/depth [1-4] \/ 5/);
  const board = page.getByRole('grid', { name: 'copenhagen game board' });
  const piece = board.getByRole('gridcell', { name: /attacker/ }).first();
  await expect(piece).toBeEnabled();
  await piece.click();
  const destination = board.getByRole('gridcell', { name: /empty, legal move/ }).first();
  await expect(destination).toBeEnabled();
  await expect(panel).toHaveAttribute('aria-busy', 'true');
  await destination.click();
  await showMove(page, 1);
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await openGameOptions(page);
  await advisor.selectOption('profile:baseline');
  await gameTab(page, 'analytics');
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await expect(advisorOpinion).toContainText('Shield bearer');
  await expect(advisorOpinion).not.toContainText('Custom move advisor');
  await expect(board.locator('.hint-overlay')).toBeVisible();
});

test('advisor highlights a legal move and original estimates survive replay, reload and localization', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const stored = await historicalGame(request, {
    data: { ruleset: 'brandubh', mode: 'human-human' },
  });
  expect(stored.ok()).toBe(true);
  await page.goto('/games/' + (await stored.json()).id);
  await openGameOptions(page);
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = analytics.getByRole('combobox', { name: 'Move advisor', exact: true });
  await expect(advisor).toHaveValue('');
  await expect(
    analytics.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }),
  ).toHaveValue('5');
  await advisor.selectOption('profile:baseline');
  await gameTab(page, 'analytics');
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  const advisorOpinion = panel
    .locator('.agent-estimate')
    .filter({ has: page.getByText('Move advisor', { exact: true }) });
  await expect(advisorOpinion.getByRole('meter')).toHaveCount(1);
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  const initialValue = await advisorOpinion.getByRole('meter').getAttribute('aria-valuenow');
  const board = page.getByRole('grid', { name: 'brandubh game board' });
  await expect(board.locator('.hint-overlay')).toBeVisible();
  await expect(board.locator('.hint-source')).toHaveCount(1);
  await expect(board.locator('.hint-target')).toHaveCount(1);
  // The advisor selection is the single trigger; no visibility checkbox remains.
  await expect(panel.getByRole('checkbox')).toHaveCount(0);
  await expect(advisorOpinion.getByRole('meter')).toHaveCount(1);
  await expect(board.locator('.hint-overlay')).toBeVisible();
  await board.locator('.hint-source').click();
  await board.locator('.hint-target').click();
  await showMove(page, 1);
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await gameTab(page, 'analytics');
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await gameTab(page, 'moves');
  await page.getByRole('button', { name: /Starting position/ }).click();
  await gameTab(page, 'analytics');
  await expect(advisorOpinion.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);
  await page.reload();
  await gameTab(page, 'moves');
  await page.getByRole('button', { name: /Starting position/ }).click();
  await gameTab(page, 'analytics');
  await expect(advisorOpinion.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);
  await openGameOptions(page);
  await expect(advisor).toHaveValue('profile:baseline');
  const gamePath = new URL(page.url()).pathname;
  await openGameNavigation(page);
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
  await returnToGame(page, gamePath);
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await gameTab(page, 'analytics');
  await expect(page.getByRole('region', { name: 'Agentenbewertungen', exact: true })).toBeVisible();
  await expect(page.getByRole('gridcell', { name: /König/ })).toHaveCount(1);
  await openGameNavigation(page);
  await openProfile(page);
  await page.getByRole('radio', { name: 'Holz', exact: true }).check();
  await openDestination(page, 'dashboard');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await returnToGame(page, gamePath);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await gameTab(page, 'analytics');
  await expect(page.getByRole('region', { name: 'Agentenbewertungen', exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});

test('the actual AI decision and the opponent’s background estimate survive replay together', async ({
  page,
  request,
}) => {
  const response = await historicalGame(request, {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 3,
      players: {
        attackers: {
          name: 'Evaluation Hugin',
          config: { algorithm: 'baseline', time_limit: 0.05 },
        },
        defenders: {
          name: 'Evaluation Munin',
          config: { algorithm: 'alpha_beta', depth: 1, time_limit: 0.05 },
        },
      },
    },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  let background: { evaluations: { agent_id: string }[] } = { evaluations: [] };
  await expect
    .poll(async () => {
      const evaluated = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/analysis`, {
        data: { version: game.version, cursor: 0, background: true },
      });
      expect(evaluated.ok()).toBe(true);
      background = await evaluated.json();
      return background.evaluations.some((agent) => agent.agent_id === 'defenders');
    })
    .toBe(true);
  // The acting agent is evaluated by the real move search, never by an advance search.
  expect(background.evaluations.some((agent) => agent.agent_id === 'attackers')).toBe(false);
  const moved = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/ai-move`, {
    data: { version: game.version },
  });
  expect(moved.ok()).toBe(true);
  const after = await moved.json();
  const rewound = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/seek`, {
    data: { version: after.version, cursor: 0 },
  });
  expect(rewound.ok()).toBe(true);
  const replay = await rewound.json();
  const decision = replay.analysis.evaluations.find(
    (agent: { agent_id: string }) => agent.agent_id === 'attackers',
  );
  expect(decision.name).toBe('Evaluation Hugin');
  expect(decision.best_move).toMatchObject({
    source: after.moves[0].source,
    target: after.moves[0].target,
  });
  expect(replay.analysis.evaluations).toHaveLength(2);
  // Loading an older saved position opens read-only history without advancing AI.
  await page.goto('/games/' + game.id);
  // The advisor selection is the display trigger; stored playing-agent evidence
  // still appears alongside the advisor.
  await openGameOptions(page);
  await page
    .getByRole('region', { name: 'Analytics', exact: true })
    .getByRole('combobox', { name: 'Move advisor', exact: true })
    .selectOption('profile:baseline');
  await gameTab(page, 'analytics');
  await expect(page.getByRole('region', { name: 'Agent evaluations' })).toBeVisible();
  for (const agent of replay.analysis.evaluations) {
    const selector = page.getByRole('combobox', { name: 'Agent evaluations', exact: true });
    if (await selector.isVisible()) await selector.selectOption(agent.agent_id);
    await expect(
      page.getByRole('meter', { name: `${agent.name} estimate for Attackers` }),
    ).toHaveAttribute('aria-valuenow', String(Math.round(agent.win_probability * 1000) / 10));
  }
  await expect(page.getByText('For the current player: Attackers', { exact: true })).toBeVisible();
});
