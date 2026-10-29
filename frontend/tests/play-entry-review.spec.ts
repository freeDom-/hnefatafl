import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';
import { gameTab, openGameNavigation } from './play-helpers';

test('Play has the shared creation dialogs when no game is running', async ({ page }) => {
  await page.route('**/api/personal/**', (route) => route.fulfill({ json: { games: [] } }));
  await page.goto('/play');
  await expect(page.getByText('No games are currently running.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Find online games on Dashboard' })).toHaveAttribute(
    'href',
    '/dashboard',
  );
  await page.getByRole('button', { name: 'New online game' }).click();
  const online = page.getByRole('dialog', { name: 'Create an online game' });
  await expect(online).toBeVisible();
  await expect(online.getByText(/At least 1 minute/)).toHaveCount(0);
  await expect(online.getByRole('button', { name: 'Cancel', exact: true })).toHaveClass(
    /secondary/,
  );
  await online.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'New single-player game' }).click();
  await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toBeVisible();
  await page.getByRole('button', { name: 'To the table' }).click();
  await expect(page.getByRole('grid')).toBeVisible();
  if (await page.locator('.desktop-game-layout').isVisible()) {
    const metadata = page.locator('.player-game-details');
    await expect(metadata).toContainText('Game mode');
    await expect(metadata).toContainText('Random seed');
    await expect(metadata.locator('dt', { hasText: /^Move$/ })).toHaveCount(0);
  }
});

test('switching games and returning through Play preserves the selected tab', async ({
  page,
  request,
}) => {
  const one = await (
    await historicalGame(request, { data: { ruleset: 'copenhagen', mode: 'human-human' } })
  ).json();
  const two = await (
    await historicalGame(request, { data: { ruleset: 'tablut', mode: 'human-human' } })
  ).json();
  await page.goto(`/games/${one.id}`);
  await gameTab(page, 'games');
  await page.locator(`[data-game-id="${two.id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/games/${two.id}$`));
  await expect(page.getByRole('tab', { name: 'My games', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await openGameNavigation(page);
  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name: 'Play', exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/games/${two.id}$`));
  await expect(page.getByRole('tab', { name: 'My games', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('Hall replay uses the play layout at reduced desktop heights and on mobile without writes', async ({
  page,
  request,
}, info) => {
  const created = await historicalGame(request, {
    data: { ruleset: 'copenhagen', mode: 'human-human' },
  });
  let game = await created.json();
  const moved = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/moves`, {
    data: { ...game.legal_moves[0], version: game.version },
  });
  expect(moved.ok()).toBe(true);
  game = await moved.json();
  await page.goto(`/archive?ruleset=brandubh&review=account-single-player&selected=${game.id}`);
  await expect(page.getByRole('grid')).toBeVisible();
  const sizes =
    info.project.name === 'mobile'
      ? [
          { width: 390, height: 780 },
          { width: 844, height: 390 },
        ]
      : [
          { width: 1920, height: 730 },
          { width: 1366, height: 620 },
        ];
  for (const size of sizes) {
    await page.setViewportSize(size);
    for (const density of ['compact', 'default', 'large']) {
      await page.locator('html').evaluate((root, value) => (root.dataset.density = value), density);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await gameTab(page, 'moves');
      await expect(page.getByRole('button', { name: 'Next move', exact: true })).toBeVisible();
      const geometry = await page.evaluate(() => {
        const board = document.querySelector('.board')!.getBoundingClientRect();
        const sidebar = document.querySelector('.game-sidebar')?.getBoundingClientRect();
        const footer = document.querySelector('.site-footer')!.getBoundingClientRect();
        const fits =
          document.documentElement.scrollHeight <= innerHeight + 1 &&
          document.documentElement.scrollWidth <= innerWidth &&
          board.top >= 0 &&
          board.bottom <= innerHeight &&
          (!sidebar || sidebar.bottom <= footer.top + 1);
        return {
          fits,
          height: document.documentElement.scrollHeight,
          width: document.documentElement.scrollWidth,
          header: document.querySelector('.site-header')!.getBoundingClientRect().toJSON(),
          board: board.toJSON(),
          sidebar: sidebar?.toJSON(),
          footer: footer.toJSON(),
          summary: [...document.querySelectorAll('.game-sidebar > *')].map((node) => ({
            class: node.className,
            ...node.getBoundingClientRect().toJSON(),
          })),
        };
      });
      expect(
        geometry.fits,
        `${size.width}×${size.height}, ${density}: ${JSON.stringify(geometry)}`,
      ).toBe(true);
      if (density === 'default' || density === 'large')
        await page.screenshot({ path: info.outputPath(`review-${size.width}-${density}.png`) });
    }
  }
  await page.getByRole('button', { name: 'Next move', exact: true }).click();
  await expect(page.getByText('Move 1 of 1')).toBeVisible();
  await expect(page.getByRole('gridcell').first()).toBeDisabled();
  const after = await (await request.get(`http://127.0.0.1:8001/api/games/${game.id}`)).json();
  expect(after).toEqual(game);
  await page.getByRole('link', { name: 'Back to Hall of records' }).click();
  await expect(page.getByRole('combobox', { name: 'Ruleset', exact: true })).toHaveValue(
    'brandubh',
  );
  await expect(page.getByRole('link', { name: 'Return to selected review' })).toBeVisible();
});

test('an unverified account sees a red Dashboard warning instead of the lobby label', async ({
  page,
}) => {
  // Keep the worker's shared administrator session valid for subsequent tests.
  await page.context().clearCookies();
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('BrowserUser');
  await page.getByLabel('Password', { exact: true }).fill('browser-fixture-only-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  const warning = page.getByText(
    'Verify your email address before creating or joining online games.',
  );
  await expect(warning).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toHaveCount(1);
  const warningBox = await warning.boundingBox();
  const actionsBox = await page
    .getByRole('button', { name: 'New online game', exact: true })
    .boundingBox();
  expect(warningBox!.y + warningBox!.height).toBeLessThanOrEqual(actionsBox!.y);
  const color = await warning.evaluate((node) =>
    getComputedStyle(node).color.match(/\d+/g)!.map(Number),
  );
  expect(color[0]).toBeGreaterThan(color[1]);
  await expect(page.getByRole('button', { name: 'New online game' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'New single-player game' })).toBeEnabled();
  await expect(page.getByText(/Creating and joining require an active/)).toHaveCount(0);
});
