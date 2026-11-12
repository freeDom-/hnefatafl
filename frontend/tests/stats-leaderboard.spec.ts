import { expect, test } from './auth-fixture';

/** A page of a rated pool plus a standing that is deliberately off that page. */
const standings = (offset: number) => {
  const entries = Array.from({ length: 25 }, (_, index) => ({
    account_id: `account-${offset + index}`,
    name: `Player ${offset + index}`,
    username: `player${offset + index}`,
    rating: 2000 - offset - index,
    rated_games: 20,
    wins: 10,
    losses: 8,
    draws: 2,
    rank: offset + index + 1,
    provisional: false,
  }));
  return {
    ruleset: 'copenhagen',
    policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
    entries,
    total: 60,
    limit: 25,
    offset,
    me: {
      account_id: 'standing-me',
      name: 'Browser Admin',
      username: 'BrowserAdmin',
      rating: 1616,
      rated_games: 30,
      wins: 20,
      losses: 8,
      draws: 2,
      rank: 42,
      provisional: false,
    },
  };
};

const ledgerItem = (
  sequence: number,
  rating_after: number,
  recorded_at: string | null,
  corrected: boolean,
) => ({
  sequence,
  game_id: `game-${sequence}`,
  recorded_at,
  rating_after,
  delta: 10,
  result: 'win' as const,
  opponent_display_name: 'BrowserUser',
  opponent_username: 'BrowserUser',
  corrected,
});

const ratingHistory = (ruleset: string) => ({
  ruleset,
  policy_version: 1,
  current_rating: ruleset === 'brandubh' ? 1530 : null,
  as_of: '2025-02-01T00:00:00Z',
  legacy_timestamp_gaps: ruleset === 'brandubh',
  items:
    ruleset === 'brandubh'
      ? [
          ledgerItem(9, 1530, '2025-01-03T12:00:00Z', false),
          ledgerItem(7, 1520, '2025-01-02T12:00:00Z', true),
          ledgerItem(3, 1510, null, false),
        ]
      : [],
  next_cursor: null,
  limit: 100,
  matching: ruleset === 'brandubh' ? 3 : 0,
});

test('statistics and leaderboard show real empty states without inventing a curve', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto('/statistics?mode=multiplayer&ruleset=tablut');
  await expect(page.getByRole('heading', { name: 'Rating history' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Local 1v1' })).toBeVisible();
  // The fixture account has no applied rating events: no chart is fabricated.
  await expect(page.getByText('No rated games in this pool yet.').first()).toBeVisible();
  await expect(page.locator('.rating-chart')).toHaveCount(0);
  // Local 1v1 is its own non-Elo category with no per-side personal split.
  await page.getByRole('tab', { name: 'Local 1v1' }).click();
  await expect(page).toHaveURL(/mode=local/);
  await expect(page.getByText('No local 1v1 games recorded yet.')).toBeVisible();
  await expect(page.getByLabel('Ruleset')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );

  await page.goto('/leaderboard');
  await expect(page.getByRole('group', { name: 'Your rank' })).toBeVisible();
  await expect(page.getByText('You are not ranked in this pool yet.')).toBeVisible();
  await expect(page.getByText('No rated games in this pool yet.')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('the full leaderboard keeps the signed-in rank visible outside the page', async ({ page }) => {
  await page.route(/\/api\/leaderboard\?/, (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') ?? '0');
    return route.fulfill({ json: standings(offset) });
  });
  await page.goto('/leaderboard');
  const rank = page.getByRole('group', { name: 'Your rank' });
  await expect(rank).toContainText('#42');
  await expect(rank).toContainText('Elo 1,616');
  await expect(rank).toContainText('30 rated games');
  await expect(rank).toContainText('Not on this page.');
  await expect(page.getByText('60 ranked players in this pool.')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Player 0' })).toBeVisible();
  await expect(page.getByText('Page 1 of 3')).toBeVisible();

  await page.getByRole('button', { name: 'Next page' }).click();
  await expect(page.getByRole('cell', { name: 'Player 25' })).toBeVisible();
  await expect(page.getByText('Page 2 of 3')).toBeVisible();
  // The standing summary survives pagination.
  await expect(rank).toContainText('#42');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('the statistics page renders the applied ledger with gaps and corrections labelled', async ({
  page,
}) => {
  await page.route(/\/api\/personal\/online\?/, (route) =>
    route.fulfill({
      json: { category: 'online', statistics: { groups: [], coverage: { unattributed_games: 0 } } },
    }),
  );
  await page.route(/\/api\/personal\/progress$/, (route) =>
    route.fulfill({
      json: {
        as_of: '2025-02-01T00:00:00Z',
        streaks: {
          rated_win_current: 2,
          rated_win_best: 4,
          by_ruleset: [
            { ruleset: 'brandubh', rated_win_current: 2, rated_win_best: 3, rated_games: 3 },
          ],
        },
      },
    }),
  );
  await page.route(/\/api\/personal\/ratings\/([a-z]+)\/history/, (route) =>
    route.fulfill({ json: ratingHistory(/ratings\/([a-z]+)\//.exec(route.request().url())![1]) }),
  );
  await page.goto('/statistics?mode=multiplayer&ruleset=brandubh');
  await expect(page.getByRole('img', { name: 'Brandubh rating progression' })).toBeVisible();
  await expect(page.getByText('1 entries have no recorded date')).toBeVisible();
  await expect(page.getByText('Rated games: 3')).toBeVisible();
  await page.getByText('Rating entries').first().click();
  await expect(page.getByText('Corrected result')).toBeVisible();
  await expect(page.getByText('Date unavailable')).toBeVisible();

  await page.locator('.language-select select').selectOption('de');
  await expect(page.getByRole('heading', { name: 'Wertungsverlauf' })).toBeVisible();
  await expect(page.getByText('Korrigiertes Ergebnis')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
