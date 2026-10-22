import { expect, test, type Page } from '@playwright/test';

const tournamentApi = 'http://127.0.0.1:8001/api/tournaments';

async function openTournaments(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
}

async function setUpTournament(
  page: Page,
  name: string,
  maxPlies = 2,
  seconds = 0.01,
  games = 2,
  opponents = 2,
) {
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
  for (let extra = 2; extra < opponents; extra++)
    await setup.getByRole('button', { name: 'Add opponent', exact: true }).click();
  for (let index = 1; index <= opponents; index++) {
    await setup.getByLabel(`Opponent ${index} profile`, { exact: true }).selectOption('baseline');
    await setup
      .getByLabel(`Opponent ${index} time limit (seconds)`, { exact: true })
      .fill(String(seconds));
  }
  await expect(setup.getByRole('textbox')).toHaveCount(1);
  await expect(setup.getByLabel(/Opponent \d+ name/, { exact: true })).toHaveCount(0);
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
  expect(recorded.config.players.map((player: { name: string }) => player.name)).toEqual([
    'Shield bearer',
    'Shield bearer',
  ]);
  await expect(detail.locator('.tournament-standing h3')).toHaveText([
    'Shield bearer',
    'Shield bearer',
  ]);
  await expect(detail.locator('.tournament-standings')).toContainText('Unfinished');
  await expect(detail.locator('.tournament-matches')).toContainText('Move limit reached');
  await expect(detail.getByRole('button', { name: 'Replay game 1', exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(detail.locator('.tournament-standing h3')).toHaveText([
    'Shield bearer',
    'Shield bearer',
  ]);
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

test('four opponents complete every balanced pairing and retain the last pair for replay', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = `Four ravens ${info.project.name}`;
  await openTournaments(page);
  await setUpTournament(page, name, 1, 0.01, 2, 4);
  const detail = page.locator('.tournament-detail');
  await expect(detail.locator('.tournament-status')).toHaveText('Completed', { timeout: 30000 });
  await expect(detail.locator('.tournament-standing')).toHaveCount(4);
  await expect(detail.locator('.tournament-match')).toHaveCount(12);
  const entries = await (await request.get(tournamentApi)).json();
  const saved = entries.find((entry: { name: string }) => entry.name === name);
  const recorded = await (await request.get(`${tournamentApi}/${saved.id}`)).json();
  expect(recorded.progress).toEqual({ completed_games: 12, total_games: 12 });
  expect(recorded.result.scores).toEqual([0, 0, 0, 0]);
  expect(recorded.result.standings.map((row: { games: number }) => row.games)).toEqual([
    6, 6, 6, 6,
  ]);
  const assignments = recorded.result.games.map(
    (match: { attacker_player: number; defender_player: number }) =>
      `${match.attacker_player}-${match.defender_player}`,
  );
  expect(new Set(assignments).size).toBe(12);
  expect(assignments).toContain('2-3');
  expect(assignments).toContain('3-2');
  const last = recorded.result.games[11];
  const lastMatch = detail.locator('.tournament-match').last();
  await expect(lastMatch).toContainText(recorded.config.players[last.attacker_player].name);
  await expect(lastMatch).toContainText(recorded.config.players[last.defender_player].name);
  await page.reload();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(detail.locator('.tournament-standing')).toHaveCount(4);
  await detail.getByRole('button', { name: 'Replay game 12', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Replay game 12', exact: true });
  await expect(dialog.locator('.tournament-replay-players')).toHaveText(
    `${recorded.config.players[last.attacker_player].rune}${recorded.config.players[last.attacker_player].name} · Attackers vs ${recorded.config.players[last.defender_player].rune}${recorded.config.players[last.defender_player].name} · Defenders`,
  );
  const replay = await (await request.get(`${tournamentApi}/${saved.id}/games/11/replay`)).json();
  expect(replay.validated).toBe(true);
  await expect(dialog.getByRole('gridcell')).toHaveCount(49);
  await dialog.getByRole('button', { name: 'Last tournament move', exact: true }).click();
  await expect
    .poll(() =>
      dialog
        .getByRole('gridcell')
        .evaluateAll((cells) => cells.map((cell) => cell.getAttribute('aria-label'))),
    )
    .toEqual(squareLabels(replay.states[1].board, 7));
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  await dialog.getByRole('button', { name: 'Close tournament replay', exact: true }).click();
  await page.getByRole('button', { name: `Delete tournament ${name}`, exact: true }).click();
  await page
    .getByRole('dialog', { name: `Delete tournament ${name}?`, exact: true })
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: `View tournament ${name}`, exact: true }),
  ).toHaveCount(0);
  expect((await request.get(`${tournamentApi}/${saved.id}`)).status()).toBe(404);
  expect(errors).toEqual([]);
});
