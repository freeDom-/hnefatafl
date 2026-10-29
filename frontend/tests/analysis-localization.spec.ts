import {
  openGameNavigation,
  returnToGame,
  openGameOptions,
  gameTab,
  showMove,
} from './play-helpers';
import { historicalGame } from './historical-fixture';
import { expect, test } from './auth-fixture';

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
  await page.getByRole('button', { name: 'Darstellungseinstellungen', exact: true }).click();
  await page.getByRole('radio', { name: 'Holz', exact: true }).check();
  await page
    .getByRole('button', { name: 'Darstellungseinstellungen schließen', exact: true })
    .click();
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
