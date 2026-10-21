import { expect, test, type APIRequestContext } from '@playwright/test';

const api = 'http://127.0.0.1:8001/api';

async function completedAI(request: APIRequestContext, name: string) {
  const created = await request.post(`${api}/games`, {
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
  const created = await request.post(`${api}/games`, {
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

test('record tables sort numeric values, filter names independently and keep AI rows compact', async ({
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

  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  const battlefields = page.getByRole('table', { name: 'Results by battlefield', exact: true });
  const players = page.getByRole('table', { name: 'Player & AI performance', exact: true });
  await expect(battlefields).toBeVisible();
  await expect(players).toBeVisible();

  // Every displayed battlefield column exposes an accessible two-way sort.
  const battlefieldHeaders = battlefields.locator('thead th');
  await expect(battlefieldHeaders).toHaveCount(6);
  for (let column = 0; column < 6; column++) {
    const header = battlefieldHeaders.nth(column);
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'descending');
  }

  const playerFilter = page.getByRole('searchbox', { name: 'Filter players', exact: true });
  const gameFilter = page.getByRole('searchbox', { name: 'Filter recorded games', exact: true });
  await playerFilter.fill(prefix.toUpperCase());
  const playerRows = players.locator('tbody tr');
  await expect(playerRows).toHaveCount(3);
  await players.getByRole('button', { name: 'Sort by Played', exact: true }).click();
  await expect
    .poll(() =>
      playerRows.evaluateAll((rows) =>
        rows.map((row) => Number(row.querySelector('td')?.textContent)),
      ),
    )
    .toEqual([2, 8, 10]);
  await players.getByRole('button', { name: 'Sort by Played', exact: true }).click();
  await expect
    .poll(() =>
      playerRows.evaluateAll((rows) =>
        rows.map((row) => Number(row.querySelector('td')?.textContent)),
      ),
    )
    .toEqual([10, 8, 2]);
  await players.getByRole('button', { name: 'Sort by Player', exact: true }).click();
  await expect(playerRows.nth(0)).toContainText(names[0]);
  await expect(playerRows.nth(1)).toContainText(names[1]);
  await expect(playerRows.nth(2)).toContainText(names[2]);

  const originalCount = await page
    .locator('.metric')
    .filter({ hasText: 'BATTLES RECORDED' })
    .locator('strong')
    .innerText();
  await gameFilter.fill(names[2].toUpperCase());
  const savedRows = page.locator('.records-list .record-entry');
  await expect(savedRows).toHaveCount(2);
  await expect(savedRows.nth(0)).toContainText(names[2]);
  await expect(savedRows.nth(1)).toContainText(names[2]);
  await expect(playerRows).toHaveCount(3);
  await expect(
    page.locator('.metric').filter({ hasText: 'BATTLES RECORDED' }).locator('strong'),
  ).toHaveText(originalCount);

  await playerFilter.fill(aiName.toLowerCase());
  const aiRows = players.locator('tbody tr');
  await expect(aiRows).toHaveCount(1);
  await expect(aiRows).toContainText('Computer');
  await expect(aiRows).toContainText('Jarl');
  await expect(aiRows).toContainText('Alpha-beta');
  await expect(aiRows).toContainText('depth 1');
  await expect(aiRows).toContainText('0.1s');
  await expect(aiRows.locator('details')).toHaveCount(0);
  await aiRows
    .getByRole('button', { name: `Show AI parameters for ${aiName}`, exact: true })
    .click();
  const parameters = page.getByRole('dialog', { name: `${aiName} AI parameters`, exact: true });
  await expect(parameters.getByText('Search depth', { exact: true })).toBeVisible();
  await expect(parameters.getByText('Random seed', { exact: true })).toHaveCount(0);
  await expect(
    parameters.getByText('Game seeds are recorded per game and are excluded from this comparison.'),
  ).toBeVisible();
  await parameters.getByRole('button', { name: 'Close AI parameters', exact: true }).click();
  await expect(savedRows).toHaveCount(2);

  const playerHeaders = players.locator('thead th');
  await expect(playerHeaders).toHaveCount(5);
  // A new key always starts ascending; Player was the most recent active key.
  for (const column of [1, 2, 3, 4]) {
    const header = playerHeaders.nth(column);
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await header.getByRole('button').click();
    await expect(header).toHaveAttribute('aria-sort', 'descending');
  }
  await gameFilter.fill('no matching recorded participant');
  await expect(page.locator('.records-list .record-entry')).toHaveCount(0);
  await expect(aiRows).toHaveCount(1);
  await page.getByRole('button', { name: 'Clear recorded game filter', exact: true }).click();
  await expect(gameFilter).toHaveValue('');
  await page.getByRole('button', { name: 'Clear player filter', exact: true }).click();
  await expect(playerFilter).toHaveValue('');
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});
