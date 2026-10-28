import { historicalGame } from './historical-fixture';
import { expect, test, type APIRequestContext } from './auth-fixture';

const api = 'http://127.0.0.1:8001/api';

async function completedAI(request: APIRequestContext, name: string) {
  const created = await historicalGame(request, {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 3,
      players: {
        attackers: {
          name,
          config: { algorithm: 'alpha_beta', depth: 1, time_limit: 0.1 },
        },
        defenders: {
          name: `Table guardian ${name.replace('Table Jarl ', '')}`,
          config: { algorithm: 'baseline', time_limit: 0.1 },
        },
      },
    },
  });
  expect(created.ok()).toBe(true);
  let game = await created.json();
  for (let ply = 0; !game.winner && ply < 100; ply++) {
    const moved = await request.post(`${api}/games/${game.id}/ai-move`, {
      data: { version: game.version },
    });
    expect(moved.ok()).toBe(true);
    game = await moved.json();
  }
  expect(game.winner).toBeTruthy();
  return game as { id: string; moves: { source: number; target: number }[] };
}

async function replayAsHumans(
  request: APIRequestContext,
  moves: { source: number; target: number }[],
  attacker: string,
  defender: string,
) {
  const created = await historicalGame(request, {
    data: {
      ruleset: 'brandubh',
      players: { attackers: { name: attacker }, defenders: { name: defender } },
    },
  });
  expect(created.ok()).toBe(true);
  let game = await created.json();
  for (const move of moves) {
    const moved = await request.post(`${api}/games/${game.id}/moves`, {
      data: { ...move, version: game.version },
    });
    expect(moved.ok()).toBe(true);
    game = await moved.json();
  }
  expect(game.winner).toBeTruthy();
}

test('compact records filter server-side by names and category with independent selected review', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const prefix = `Table ${info.project.name}`;
  const names = [`${prefix} Asta`, `${prefix} Brynja`, `${prefix} Zigfrid`];
  const aiName = `Table Jarl ${info.project.name}`;
  const recorded = await completedAI(request, aiName);
  // Reuse a real legal game record as human moves so the three identities have
  // 10, 8 and 2 completed appearances. This catches lexical sorting of counts.
  for (let game = 0; game < 10; game++) {
    await replayAsHumans(request, recorded.moves, names[0], game < 2 ? names[2] : names[1]);
  }

  await page.goto('/archive');
  await page.getByLabel('Game type').selectOption('account-single-player');
  await page.getByLabel('Player name').fill(names[2].toUpperCase());
  await expect(page.locator('.recorded-games tbody tr')).toHaveCount(2);
  await expect(page.locator('.hall-totals')).toContainText('2 Games');
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('tablut');
  await expect(page.locator('.recorded-games tbody tr')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('brandubh');
  await expect(page.locator('.recorded-games tbody tr')).toHaveCount(2);
  await page.getByLabel('Player name').fill(aiName);
  await expect(page.locator('.recorded-games tbody tr')).toHaveCount(1);
  await page.locator('.recorded-games a').click();
  await expect(page.getByRole('grid')).toBeVisible();
  await page.getByLabel('Player name').fill('no matching participant');
  await expect(page.locator('.recorded-games tbody tr')).toHaveCount(0);
  await expect(page.getByRole('grid')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
