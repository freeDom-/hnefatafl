import { expect, test, type Page } from '@playwright/test';

const tournamentApi = 'http://127.0.0.1:8001/api/tournaments';

async function openTournaments(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
}

async function setUpTournament(page: Page, name: string, maxPlies = 2, seconds = 0.01, games = 2) {
  await page.getByRole('button', { name: 'New tournament', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Set up a tournament' });
  await setup.getByRole('textbox', { name: 'Tournament name', exact: true }).fill(name);
  const battlefields = setup.getByRole('checkbox');
  for (let index = 0; index < (await battlefields.count()); index++) {
    await battlefields.nth(index).uncheck();
  }
  await setup.getByRole('checkbox', { name: 'Brandubh 7 × 7', exact: true }).check();
  await setup.getByLabel('Games per battlefield', { exact: true }).fill(String(games));
  await setup.getByLabel('Move limit per game', { exact: true }).fill(String(maxPlies));
  await setup.getByLabel('Tournament seed', { exact: true }).fill('19');
  for (const index of [1, 2]) {
    await setup.getByLabel(`Opponent ${index} name`, { exact: true }).fill(`Raven ${index}`);
    await setup.getByLabel(`Opponent ${index} profile`, { exact: true }).selectOption('baseline');
    await setup
      .getByLabel(`Opponent ${index} time limit (seconds)`, { exact: true })
      .fill(String(seconds));
  }
  await setup.getByRole('button', { name: 'Start tournament', exact: true }).click();
  await expect(setup).not.toBeVisible();
}

function squareLabels(board: string[], size: number) {
  const pieces: Record<string, string> = { '.': 'empty', A: 'attacker', D: 'defender', K: 'king' };
  return board.map(
    (piece, index) =>
      `${String.fromCharCode(97 + (index % size))}${size - Math.floor(index / size)}, ${pieces[piece]}`,
  );
}

test('tournaments retain results, expose parameters and replay moves, and delete after confirmation', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = `Raven games ${info.project.name}`;
  await openTournaments(page);
  await setUpTournament(page, name);
  const detail = page.locator('.tournament-detail');
  await expect(detail).toContainText(name);
  await expect(detail.locator('.tournament-status')).toHaveText('Completed', { timeout: 30000 });

  const saved = await (await request.get(tournamentApi)).json();
  const tournament = saved.find((entry: { name: string }) => entry.name === name);
  expect(tournament).toBeTruthy();
  const response = await request.get(`${tournamentApi}/${tournament.id}`);
  expect(response.ok()).toBe(true);
  const recorded = await response.json();
  expect(recorded.status).toBe('completed');
  expect(recorded.progress).toEqual({ completed_games: 2, total_games: 2 });
  expect(recorded.result.truncated).toBe(2);
  expect(recorded.result.scores).toEqual([0, 0]);
  expect(
    recorded.result.games.map((game: { attacker_player: number }) => game.attacker_player),
  ).toEqual([0, 1]);
  await expect(detail.locator('.tournament-standings')).toContainText('Raven 1');
  await expect(detail.locator('.tournament-standings')).toContainText('Raven 2');
  await expect(detail.locator('.tournament-standings')).toContainText('Unfinished');
  await expect(detail.locator('.tournament-matches')).toContainText('Move limit reached');
  await expect(detail.getByRole('button', { name: 'Replay game 1', exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(detail.locator('.tournament-standings')).toContainText('Raven 1');
  await detail.getByText('Opponent 1 parameters', { exact: false }).click();
  await expect(detail.locator('details[open]')).toContainText('0.01');
  await expect(detail).toContainText('19');

  await detail.getByRole('button', { name: 'Replay game 1', exact: true }).click();
  const replayResponse = await request.get(`${tournamentApi}/${tournament.id}/games/0/replay`);
  expect(replayResponse.ok()).toBe(true);
  const replay = await replayResponse.json();
  expect(replay.validated).toBe(true);
  expect(replay.states).toHaveLength(3);
  const board = page.getByRole('grid', { name: 'brandubh game board' });
  await expect(board).toBeVisible();
  await expect
    .poll(() =>
      board
        .getByRole('gridcell')
        .evaluateAll((cells) => cells.map((cell) => cell.getAttribute('aria-label'))),
    )
    .toEqual(squareLabels(replay.states[0].board, 7));
  await page.getByRole('button', { name: 'Next tournament move', exact: true }).click();
  await expect
    .poll(() =>
      board
        .getByRole('gridcell')
        .evaluateAll((cells) => cells.map((cell) => cell.getAttribute('aria-label'))),
    )
    .toEqual(squareLabels(replay.states[1].board, 7));
  await page.getByRole('button', { name: 'Last tournament move', exact: true }).click();
  await expect
    .poll(() =>
      board
        .getByRole('gridcell')
        .evaluateAll((cells) => cells.map((cell) => cell.getAttribute('aria-label'))),
    )
    .toEqual(squareLabels(replay.states[2].board, 7));
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  await page.getByRole('button', { name: 'Close tournament replay', exact: true }).click();

  await page.getByRole('button', { name: `Delete tournament ${name}`, exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: `Delete tournament ${name}?` });
  await confirmation.getByRole('button', { name: 'Keep it', exact: true }).click();
  expect((await request.get(`${tournamentApi}/${tournament.id}`)).ok()).toBe(true);
  await page.getByRole('button', { name: `Delete tournament ${name}`, exact: true }).click();
  await confirmation.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(
    page.getByRole('button', { name: `View tournament ${name}`, exact: true }),
  ).toHaveCount(0);
  expect((await request.get(`${tournamentApi}/${tournament.id}`)).status()).toBe(404);
  await openTournaments(page);
  await expect(
    page.getByRole('button', { name: `View tournament ${name}`, exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a running tournament can be stopped and its retained data deleted', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Worker cancellation is checked once on desktop.');
  const name = 'Stopped Raven games';
  await openTournaments(page);
  await setUpTournament(page, name, 1000, 0.1, 100);
  const detail = page.locator('.tournament-detail');
  await expect(detail.locator('.tournament-status')).toHaveText('In progress');
  const tournaments = await (await request.get(tournamentApi)).json();
  const tournament = tournaments.find((entry: { name: string }) => entry.name === name);
  expect(tournament).toBeTruthy();
  expect((await request.delete(`${tournamentApi}/${tournament.id}`)).status()).toBe(409);
  await page.getByRole('button', { name: 'Stop tournament', exact: true }).click();
  await expect(detail.locator('.tournament-status')).toHaveText('Stopped', { timeout: 15000 });
  const stopped = await (await request.get(`${tournamentApi}/${tournament.id}`)).json();
  expect(stopped.status).toBe('cancelled');
  expect(stopped.progress.completed_games).toBeLessThan(stopped.progress.total_games);
  await page.getByRole('button', { name: `Delete tournament ${name}`, exact: true }).click();
  await page
    .getByRole('dialog', { name: `Delete tournament ${name}?` })
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: `View tournament ${name}`, exact: true }),
  ).toHaveCount(0);
  expect((await request.get(`${tournamentApi}/${tournament.id}`)).status()).toBe(404);
});
