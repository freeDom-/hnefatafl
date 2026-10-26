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
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = analytics.getByRole('combobox', { name: 'Move advisor', exact: true });
  // Leave time for real touch scrolling between the advisor and the board while
  // asserting that input remains available during unfinished analysis.
  await analytics.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }).fill('15');
  await advisor.selectOption('custom');
  await expect(
    analytics.getByRole('spinbutton', { name: 'Search depth', exact: true }),
  ).toHaveValue('5');
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
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await advisor.selectOption('profile:baseline');
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
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  const advisor = analytics.getByRole('combobox', { name: 'Move advisor', exact: true });
  await expect(advisor).toHaveValue('');
  await expect(
    analytics.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }),
  ).toHaveValue('5');
  await advisor.selectOption('profile:baseline');
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
  const showChances = panel.getByRole('checkbox', { name: 'Show winning chances', exact: true });
  await expect(showChances).toBeChecked();
  await showChances.uncheck();
  await expect(advisorOpinion.getByRole('meter')).toHaveCount(0);
  await expect(board.locator('.hint-overlay')).toBeVisible();
  await showChances.check();
  await expect(advisorOpinion.getByRole('meter')).toHaveCount(1);
  await board.locator('.hint-source').click();
  await board.locator('.hint-target').click();
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await page.getByRole('button', { name: 'Undo move', exact: true }).click();
  await expect(advisorOpinion.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);
  await page.reload();
  await expect(advisorOpinion.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);
  await expect(advisor).toHaveValue('profile:baseline');

  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.getByRole('heading', { name: 'Gewinnchancen', exact: true })).toBeVisible();
  await expect(page.getByRole('gridcell', { name: /König/ })).toHaveCount(1);
  await page.getByRole('button', { name: 'Darstellungseinstellungen', exact: true }).click();
  await page.getByRole('radio', { name: 'Holz', exact: true }).check();
  await page
    .getByRole('button', { name: 'Darstellungseinstellungen schließen', exact: true })
    .click();
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await expect(page.getByRole('heading', { name: 'Gewinnchancen', exact: true })).toBeVisible();
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
  // Freeze only the browser playback timer while opening the retained position;
  // all recorded searches and moves above used the real engine/API.
  await page.clock.install({ time: new Date() });
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/games/' + game.id);
  await page.getByRole('button', { name: /Pause AI/ }).click();
  await page.clock.resume();
  const meters = page.getByRole('region', { name: 'Agent evaluations' }).getByRole('meter');
  await expect(meters).toHaveCount(2);
  for (const agent of replay.analysis.evaluations) {
    await expect(
      page.getByRole('meter', { name: `${agent.name} estimate for Attackers` }),
    ).toHaveAttribute('aria-valuenow', String(Math.round(agent.win_probability * 1000) / 10));
  }
  await expect(page.getByText('For the current player: Attackers', { exact: true })).toBeVisible();
});
