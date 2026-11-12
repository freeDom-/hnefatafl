import { expect, test } from './auth-fixture';

const API = 'http://127.0.0.1:8001/api';

/** Deterministic Play entry: no saved games, so the hierarchy stays on screen. */
async function emptyPlayEntry(page: import('@playwright/test').Page) {
  await page.route('**/api/personal/**', (route) => route.fulfill({ json: { games: [] } }));
}

test('Play shows the continue hierarchy and creates a real local 1v1 game', async ({
  page,
  request,
}) => {
  await emptyPlayEntry(page);
  await page.goto('/play');
  await expect(page.getByRole('heading', { name: 'Your turn / Continue' })).toBeVisible();
  await expect(page.getByText('No games are currently running.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Play online' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Local 1v1' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Play vs AI' })).toBeVisible();

  // Narrow phones stack the three equal-start modes without page overflow.
  await page.setViewportSize({ width: 320, height: 640 });
  const stacked = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.play-mode-card')].map((node) =>
      node.getBoundingClientRect().toJSON(),
    );
    return {
      count: cards.length,
      stacked: cards.every((card, index) => index === 0 || card.y >= cards[index - 1].bottom - 1),
      fits: document.documentElement.scrollWidth <= innerWidth,
    };
  });
  expect(stacked).toEqual({ count: 3, stacked: true, fits: true });

  await page.getByRole('button', { name: 'New local 1v1 game' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a local 1v1 game' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Attackers seat label').fill('Odin');
  await dialog.getByLabel('Defenders seat label').fill('Jarl');
  await dialog.getByRole('button', { name: 'Start game' }).click();

  await expect(page).toHaveURL(/\/games\/[0-9a-f]{32}$/);
  const gameId = new URL(page.url()).pathname.split('/').pop()!;
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByText('Local 1v1').first()).toBeVisible();
  // Pass-and-play keeps both server-owned seat labels next to the board.
  await expect(page.getByText('Odin').first()).toBeVisible();
  await expect(page.getByText('Jarl').first()).toBeVisible();

  // The private record stays in its own Hall scope and replays read-only.
  await page.goto('/archive?category=local');
  const review = page.getByRole('link', { name: /^Open review:/ }).first();
  await expect(review).toBeVisible();
  await expect(review).toHaveAttribute('href', new RegExp(`review=local&selected=${gameId}`));
  await review.click();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByText('Read-only replay')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Analytics' })).toHaveCount(0);

  const saved = await (await request.get(`${API}/games/${gameId}`)).json();
  expect((await request.delete(`${API}/games/${gameId}?version=${saved.version}`)).ok()).toBe(true);
});

test('Replays & Analysis keeps private records in their own server-side scope', async ({
  page,
  request,
}) => {
  const created = await request.post(`${API}/games/local`, {
    data: { ruleset: 'brandubh', attackers_label: 'Odin', defenders_label: 'Jarl' },
  });
  expect(created.status()).toBe(201);
  const game = await created.json();

  await page.goto('/archive');
  await expect(
    page.getByText(
      'Active online games open the live board; completed records open read-only replay with analysis.',
    ),
  ).toBeVisible();
  // The default projection never mixes a private local record into online history.
  await expect(page.locator(`a[href*="selected=${game.id}"]`)).toHaveCount(0);

  // Scope tabs are answered by the server with a reset cursor, never by filtering a page.
  const filtered = page.waitForRequest(
    (entry) =>
      entry.url().includes('/api/history/games') &&
      new URL(entry.url()).searchParams.get('category') === 'local',
  );
  await page.getByRole('tab', { name: 'Local 1v1' }).click();
  await filtered;
  await expect(page).toHaveURL(/category=local/);
  expect(new URL(page.url()).searchParams.get('cursor')).toBeNull();

  const review = page.getByRole('link', { name: /^Open review:/ }).first();
  await expect(review).toHaveAttribute('href', new RegExp(`review=local&selected=${game.id}`));
  await review.click();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByText('Read-only replay')).toBeVisible();
  // Local 1v1 runs no advisor, so no analysis tab is offered.
  await expect(page.getByRole('tab', { name: 'Analytics' })).toHaveCount(0);

  const saved = await (await request.get(`${API}/games/${game.id}`)).json();
  expect((await request.delete(`${API}/games/${game.id}?version=${saved.version}`)).ok()).toBe(
    true,
  );
});
