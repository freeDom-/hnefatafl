import { expect, test } from './auth-fixture';
import { openDestination } from './play-helpers';

/** Deterministic /api/personal responses so Play resume timing is controlled. */
const empty = { games: [] };
const inProgress = (id: string) => ({ games: [{ id, status: 'in_progress' }] });
const activeOnline = (id: string) => ({ games: [{ id, status: 'active' }] });

/** Resolve after the browser has had two animation frames to apply state. */
function settle(page: import('@playwright/test').Page) {
  return page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

test.describe('Play auto-resume respects newer navigation', () => {
  test('settles on the Play entry when no game is saved', async ({ page }) => {
    await page.route('**/api/personal/**', (route) => route.fulfill({ json: empty }));
    await page.goto('/play');
    await expect(page.getByText('No games are currently running.')).toBeVisible();
    await expect(page).toHaveURL(/\/play$/);
  });

  test('resumes an active single-player game', async ({ page }) => {
    await page.route('**/api/personal/single-player**', (route) =>
      route.fulfill({ json: inProgress('resumesingle') }),
    );
    await page.route('**/api/personal/online**', (route) => route.fulfill({ json: empty }));
    await page.goto('/play');
    await expect(page).toHaveURL(/\/games\/resumesingle$/);
  });

  test('resumes an active online game', async ({ page }) => {
    const id = `online-${'a'.repeat(32)}`;
    await page.route('**/api/personal/single-player**', (route) => route.fulfill({ json: empty }));
    await page.route('**/api/personal/online**', (route) =>
      route.fulfill({ json: activeOnline(id) }),
    );
    await page.goto('/play');
    await expect(page).toHaveURL(new RegExp(`/online/games/${id}$`));
  });

  test('a resume in flight cannot override a newer location', async ({ page }) => {
    // Deterministically reproduce the race: the location changes while the
    // resume request is still pending and React has not yet committed the route.
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let fulfilled: () => void = () => {};
    const didFulfill = new Promise<void>((resolve) => {
      fulfilled = resolve;
    });
    await page.route('**/api/personal/**', async (route) => {
      await gate;
      try {
        await route.fulfill({ json: inProgress('pendingresume') });
      } catch {
        /* The request may already be aborted after the newer navigation. */
      }
      fulfilled();
    });

    await page.goto('/play');
    await expect(page.getByRole('heading', { name: 'Play', exact: true })).toBeVisible();
    await page.evaluate(() => history.pushState(null, '', '/statistics'));
    release();
    await didFulfill;
    await settle(page);
    // The outdated resume must not navigate to the game.
    await expect(page).toHaveURL(/\/statistics$/);
  });

  test('an immediate Play → Statistics survives a pending resume', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let fulfilled: () => void = () => {};
    const didFulfill = new Promise<void>((resolve) => {
      fulfilled = resolve;
    });
    await page.route('**/api/personal/**', async (route) => {
      await gate;
      try {
        await route.fulfill({ json: inProgress('pendingresume') });
      } catch {
        /* Aborted after the user left Play. */
      }
      fulfilled();
    });

    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/play$/);
    await openDestination(page, 'statistics');
    await expect(page).toHaveURL(/\/statistics$/);
    release();
    await didFulfill;
    await settle(page);
    await expect(page).toHaveURL(/\/statistics$/);
    await expect(page).not.toHaveURL(/\/games\//);
  });

  test('an immediate Play → Leaderboard survives a pending resume', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/personal/**', async (route) => {
      await gate;
      try {
        await route.fulfill({ json: inProgress('pendingresume') });
      } catch {
        /* Aborted after the user left Play. */
      }
    });

    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/play$/);
    await openDestination(page, 'leaderboard');
    await expect(page).toHaveURL(/\/leaderboard$/);
    release();
    await settle(page);
    await expect(page).toHaveURL(/\/leaderboard$/);
  });

  test('browser back and forward work after leaving Play', async ({ page }) => {
    await page.route('**/api/personal/**', (route) => route.fulfill({ json: empty }));
    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/play$/);
    await openDestination(page, 'statistics');
    await expect(page).toHaveURL(/\/statistics$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/play$/);
    await page.goForward();
    await expect(page).toHaveURL(/\/statistics$/);
  });

  test('auto-resume then Browser Back returns to the previous page', async ({ page }) => {
    await page.route('**/api/personal/**', (route) =>
      route.fulfill({ json: inProgress('backresume') }),
    );
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/games\/backresume$/);
    await page.goBack();
    // The automatic resume replaces the /play entry, so Back must not land on
    // /play and immediately reopen the game.
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('Browser Forward returns to the resumed game', async ({ page }) => {
    await page.route('**/api/personal/**', (route) =>
      route.fulfill({ json: inProgress('forwardresume') }),
    );
    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/games\/forwardresume$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goForward();
    await expect(page).toHaveURL(/\/games\/forwardresume$/);
  });

  test('auto-resume with an online game survives Browser Back', async ({ page }) => {
    const id = `online-${'b'.repeat(32)}`;
    await page.route('**/api/personal/single-player**', (route) => route.fulfill({ json: empty }));
    await page.route('**/api/personal/online**', (route) =>
      route.fulfill({ json: activeOnline(id) }),
    );
    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(new RegExp(`/online/games/${id}$`));
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('resumes one of several active games', async ({ page }) => {
    await page.route('**/api/personal/single-player**', (route) =>
      route.fulfill({
        json: {
          games: [
            { id: 'multia', status: 'in_progress' },
            { id: 'multib', status: 'in_progress' },
          ],
        },
      }),
    );
    await page.route('**/api/personal/online**', (route) => route.fulfill({ json: empty }));
    await page.goto('/dashboard');
    await openDestination(page, 'play');
    await expect(page).toHaveURL(/\/games\/multi[ab]$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});
