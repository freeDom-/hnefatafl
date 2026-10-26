import { expect, test, type Page } from './auth-fixture';
import type { Tournament } from '../src/tournamentTypes';

const tournamentApi = 'http://127.0.0.1:8001/api/tournaments';

async function openTournaments(page: Page) {
  await page.goto('/dashboard?setup=single-player');
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
    await setup.getByLabel(`Opponent ${index} profile`, { exact: true }).selectOption('');
    await setup.getByLabel(`Opponent ${index} algorithm`, { exact: true }).selectOption('baseline');
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

test('tournament named opponents use fixed presets while a custom opponent keeps editable search settings', async ({
  page,
  request,
}, info) => {
  await openTournaments(page);
  await page.getByRole('button', { name: 'New tournament', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Set up a tournament' });
  const nameLabel = await setup.getByText('Tournament name', { exact: true }).boundingBox();
  const nameInput = await setup
    .getByRole('textbox', { name: 'Tournament name', exact: true })
    .boundingBox();
  expect(nameLabel).not.toBeNull();
  expect(nameInput).not.toBeNull();
  // Match the 8px label/input gap used by the other setup fields.
  expect(nameInput!.y - (nameLabel!.y + nameLabel!.height)).toBeGreaterThanOrEqual(7);
  await setup
    .getByRole('textbox', { name: 'Tournament name', exact: true })
    .fill(`Preset rivals ${info.project.name}`);
  await setup.getByLabel('Move limit per game', { exact: true }).fill('1');
  await setup.getByLabel('Games per battlefield', { exact: true }).fill('2');
  const first = setup.locator('.tournament-player').nth(0);
  const second = setup.locator('.tournament-player').nth(1);
  await first.getByLabel('Opponent 1 profile', { exact: true }).selectOption('alpha_beta');
  await second.getByLabel('Opponent 2 profile', { exact: true }).selectOption('mcts');
  await setup.getByRole('button', { name: 'Add opponent', exact: true }).click();
  const third = setup.locator('.tournament-player').nth(2);
  await third.getByLabel('Opponent 3 profile', { exact: true }).selectOption('baseline');
  await expect(third).toContainText('Tactical heuristic · 1s');
  for (const opponent of [first, second, third]) {
    await expect(opponent.getByRole('spinbutton')).toHaveCount(0);
    await expect(opponent.getByRole('combobox', { name: /algorithm/i })).toHaveCount(0);
  }
  await first.getByLabel('Opponent 1 profile', { exact: true }).selectOption('');
  await first.getByRole('combobox', { name: /Opponent 1 algorithm/i }).selectOption('alpha_beta');
  await first.getByText('Search parameters', { exact: true }).click();
  await first.getByLabel('Opponent 1 search depth', { exact: true }).fill('3');
  await first.getByLabel('Opponent 1 time limit (seconds)', { exact: true }).fill('0.05');
  await expect(first.getByLabel('Opponent 1 search depth', { exact: true })).toHaveValue('3');
  await expect(first.getByLabel('Opponent 1 time limit (seconds)', { exact: true })).toHaveValue(
    '0.05',
  );
  await first.getByLabel('Opponent 1 profile', { exact: true }).selectOption('alpha_beta');
  await expect(first.getByRole('spinbutton')).toHaveCount(0);
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/tournaments') && response.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'Start tournament', exact: true }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const tournament = await response.json();
  expect(tournament.config.players[0].config).toMatchObject({
    algorithm: 'alpha_beta',
    depth: 5,
    time_limit: 2,
  });
  expect(tournament.config.players[1].config).toMatchObject({
    algorithm: 'mcts',
    iterations: 5000,
    temperature: 0,
    exploration: 1.4,
    time_limit: 2,
  });
  expect(tournament.config.players[2].config).toMatchObject({
    algorithm: 'baseline',
    time_limit: 1,
  });
  await expect(page.locator('.tournament-detail .tournament-status')).toHaveText('Completed', {
    timeout: 30000,
  });
  expect((await request.delete(`${tournamentApi}/${tournament.id}`)).ok()).toBe(true);
});

async function expectWinMatrices(page: Page, tournament: Tournament) {
  for (const ruleset of tournament.config.rulesets) {
    const matrix = page.locator(`.tournament-win-matrix[data-ruleset="${ruleset}"]`);
    await expect(matrix).toBeVisible();
    await expect(matrix.getByRole('rowheader')).toHaveCount(tournament.config.players.length);
    await expect(matrix.getByRole('columnheader')).toHaveCount(
      tournament.config.players.length + 1,
    );
    for (let row = 0; row < tournament.config.players.length; row++) {
      const player = tournament.config.players[row];
      let rowWins = 0;
      for (let column = 0; column < tournament.config.players.length; column++) {
        const cell = matrix.locator(`td[data-row-player="${row}"][data-column-player="${column}"]`);
        if (row === column) {
          await expect(cell.locator('[data-rune]')).toHaveAttribute(
            'data-rune',
            player.agent_rune || player.rune || '',
          );
          continue;
        }
        const wins = tournament.result!.games.filter(
          (match) =>
            match.ruleset === ruleset &&
            match.outcome === 'completed' &&
            ((match.winner === 'attackers' &&
              match.attacker_player === row &&
              match.defender_player === column) ||
              (match.winner === 'defenders' &&
                match.defender_player === row &&
                match.attacker_player === column)),
        ).length;
        await expect(cell).toHaveText(String(wins));
        rowWins += wins;
      }
      expect(rowWins).toBe(tournament.result!.by_ruleset[ruleset].wins[row]);
    }
  }
}

test('battlefield win matrices show directed wins and runes, and survive reload', async ({
  page,
  request,
}, info) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = `Win matrix ${info.project.name}`;
  const created = await request.post(tournamentApi, {
    data: {
      name,
      rulesets: ['brandubh', 'tablut'],
      games: 2,
      max_plies: 100,
      seed: 19,
      // Same profile name, distinct agent settings/runes: seats must remain
      // separate on both axes even when every name is identical.
      players: Array.from({ length: 4 }, (_, index) => ({
        config: { algorithm: 'baseline', time_limit: (index + 1) * 0.05 },
      })),
    },
  });
  expect(created.status()).toBe(201);
  const started = (await created.json()) as Tournament;
  await openTournaments(page);
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(page.locator('.tournament-detail .tournament-status')).toHaveText('Completed', {
    timeout: 90000,
  });
  const saved = (await (await request.get(`${tournamentApi}/${started.id}`)).json()) as Tournament;
  expect(saved.result!.games).toHaveLength(24);
  expect(saved.result!.games.some((game) => game.winner && game.winner !== 'draw')).toBe(true);
  await expectWinMatrices(page, saved);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);

  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expectWinMatrices(page, saved);
  if (info.project.name === 'mobile') {
    const scroller = page.locator('.tournament-win-matrix-scroll').first();
    const rowHeader = scroller.getByRole('rowheader').first();
    const left = await rowHeader.evaluate((element) => element.getBoundingClientRect().left);
    await scroller.focus();
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    await scroller.evaluate((element) => (element.scrollLeft = element.scrollWidth));
    expect(await rowHeader.evaluate((element) => element.getBoundingClientRect().left)).toBeCloseTo(
      left,
      0,
    );
  }
  await page.locator('.tournament-battlefields').screenshot({
    path: info.outputPath('battlefield-win-matrices.png'),
  });
  expect(errors).toEqual([]);
  expect((await request.delete(`${tournamentApi}/${started.id}`)).ok()).toBe(true);
});

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
  await expect(detail.locator('.tournament-matches')).toBeHidden();
  await detail.getByRole('button', { name: 'Match log', exact: true }).click();
  await expect(detail.locator('.tournament-matches')).toContainText('Move limit reached');
  await expect(detail.getByRole('button', { name: 'Replay game 1', exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(detail.locator('.tournament-standing h3')).toHaveText([
    'Shield bearer',
    'Shield bearer',
  ]);
  await detail.getByText('Opponent 1 parameters', { exact: false }).click();
  await expect(detail.locator('details[open]')).toContainText('0.01');
  await expect(detail).toContainText('19');

  await detail.getByRole('button', { name: 'Match log', exact: true }).click();
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

  await page.getByRole('button', { name: 'Delete tournament data', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: `Delete tournament ${name}?` });
  await confirmation.getByRole('button', { name: 'Keep it', exact: true }).click();
  expect((await request.get(`${tournamentApi}/${tournament.id}`)).ok()).toBe(true);
  await page.getByRole('button', { name: 'Delete tournament data', exact: true }).click();
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

test('two custom alpha-beta opponents record distinct optimization settings and replay legally', async ({
  page,
  request,
}, info) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = `Alpha-beta search comparison ${info.project.name}`;
  await openTournaments(page);
  await page.getByRole('button', { name: 'New tournament', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Set up a tournament' });
  await setup.getByRole('textbox', { name: 'Tournament name', exact: true }).fill(name);
  const battlefields = setup.getByRole('checkbox');
  for (let index = 0; index < (await battlefields.count()); index++) {
    await battlefields.nth(index).uncheck();
  }
  await setup.getByRole('checkbox', { name: 'Brandubh 7 × 7', exact: true }).check();
  await setup.getByLabel('Games per battlefield', { exact: true }).fill('2');
  await setup.getByLabel('Move limit per game', { exact: true }).fill('8');
  await setup.getByLabel('Tournament seed', { exact: true }).fill('20261026');
  const configure = async (index: number, preset: 'legacy' | 'symmetry') => {
    await setup.getByLabel(`Opponent ${index} profile`, { exact: true }).selectOption('');
    await setup
      .getByLabel(`Opponent ${index} algorithm`, { exact: true })
      .selectOption('alpha_beta');
    await setup.getByLabel(`Opponent ${index} search preset`, { exact: true }).selectOption(preset);
    await setup
      .getByText('Search parameters', { exact: true })
      .nth(index - 1)
      .click();
    await setup.getByLabel(`Opponent ${index} search depth`, { exact: true }).fill('2');
    await setup.getByLabel(`Opponent ${index} time limit (seconds)`, { exact: true }).fill('0.02');
  };
  await configure(1, 'legacy');
  await configure(2, 'symmetry');
  await expect(setup.getByLabel('Opponent 1 search preset', { exact: true })).toHaveValue('legacy');
  await expect(setup.getByLabel('Opponent 2 search preset', { exact: true })).toHaveValue(
    'symmetry',
  );
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/tournaments') && response.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'Start tournament', exact: true }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const tournament = (await response.json()) as Tournament;
  expect(tournament.config.players[0].config.search).toMatchObject({
    legacy: true,
    symmetry_transposition: false,
    symmetry_pruning: false,
  });
  expect(tournament.config.players[1].config.search).toMatchObject({
    legacy: false,
    symmetry_transposition: true,
    symmetry_pruning: true,
  });
  expect(tournament.config.players[0].variant_id).not.toBe(tournament.config.players[1].variant_id);
  const detail = page.locator('.tournament-detail');
  await expect(detail.locator('.tournament-status')).toHaveText('Completed', { timeout: 60000 });
  await expect(detail.locator('.tournament-standing')).toHaveCount(2);
  // Saved records show the actual optimizations instead of an object placeholder.
  await detail.getByText('Opponent 1 parameters', { exact: false }).click();
  await expect(detail.locator('details[open]')).toContainText('Legacy search');
  await detail.getByText('Opponent 2 parameters', { exact: false }).click();
  await expect(detail.locator('details[open]').last()).toContainText('Symmetry move pruning');
  // Export the saved result document and confirm it retains both search configs.
  const download = page.waitForEvent('download');
  await detail.getByRole('button', { name: 'Export results', exact: true }).click();
  expect((await download).suggestedFilename()).toContain('hnefatafl-tournament');
  // Replay a recorded game and confirm the validator accepted the move list.
  const replayResponse = await request.get(`${tournamentApi}/${tournament.id}/games/0/replay`);
  expect(replayResponse.ok()).toBe(true);
  expect((await replayResponse.json()).validated).toBe(true);
  await detail.getByRole('button', { name: 'Match log', exact: true }).click();
  await detail.getByRole('button', { name: 'Replay game 1', exact: true }).click();
  const replayDialog = page.getByRole('dialog', { name: 'Replay game 1', exact: true });
  await expect(replayDialog.getByRole('grid')).toBeVisible();
  await replayDialog.getByRole('button', { name: 'Close tournament replay', exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect((await request.delete(`${tournamentApi}/${tournament.id}`)).ok()).toBe(true);
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
  await page.getByRole('button', { name: 'Delete tournament data', exact: true }).click();
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
  await detail.getByRole('button', { name: 'Match log', exact: true }).click();
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
  await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: `View tournament ${name}`, exact: true }).click();
  await expect(detail.locator('.tournament-standing')).toHaveCount(4);
  await detail.getByRole('button', { name: 'Match log', exact: true }).click();
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
  await page.getByRole('button', { name: 'Delete tournament data', exact: true }).click();
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
