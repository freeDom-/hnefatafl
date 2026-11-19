import { expect, test, type Page } from './auth-fixture';
import { openNewSinglePlayer } from './play-helpers';

const FORM_MODULE = '/src/NewGameForm.tsx';
const formModule = (url: string) => new URL(url).pathname === FORM_MODULE;
const dialog = (page: Page, title = 'Begin a new saga') =>
  page.getByRole('dialog', { name: title, exact: true });

/**
 * Hold the deferred form module so the eager modal shell can be inspected while
 * the real dynamic import is still pending. `loaded` resolves once the browser
 * has received the module after `release()`.
 */
async function holdFormImport(page: Page) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const loaded = page.waitForResponse((response) => formModule(response.url()));
  await page.route(formModule, async (route) => {
    await gate;
    await route.continue();
  });
  return { release, loaded };
}

async function openSetup(page: Page, query = '?setup=single-player', title = 'Begin a new saga') {
  await page.goto(`/dashboard${query}`);
  const setup = dialog(page, title);
  await expect(setup).toBeVisible();
  return setup;
}

test('keeps the modal shell, close action and loading feedback while the form import is delayed', async ({
  page,
}) => {
  const { release, loaded } = await holdFormImport(page);
  const setup = await openSetup(page);
  await expect(setup.getByRole('status')).toContainText('Loading…');
  await expect(setup.getByRole('button', { name: 'Close dialog' })).toBeVisible();
  release();
  await loaded;
  await expect(setup.getByRole('button', { name: 'To the table' })).toBeVisible();
});

test('cancelling before the form import resolves leaves the dialog closed', async ({ page }) => {
  const { release, loaded } = await holdFormImport(page);
  const setup = await openSetup(page);
  await setup.getByRole('button', { name: 'Close dialog' }).click();
  await expect(dialog(page)).toHaveCount(0);
  release();
  await loaded;
  await expect(dialog(page)).toHaveCount(0);
});

test('Escape dismisses the dialog before the form import resolves', async ({ page }) => {
  const { release, loaded } = await holdFormImport(page);
  const setup = await openSetup(page);
  await setup.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
  release();
  await loaded;
  await expect(dialog(page)).toHaveCount(0);
});

test('shows import failure feedback inside the modal and keeps it cancellable', async ({
  page,
}) => {
  await page.route(formModule, (route) => route.abort());
  const setup = await openSetup(page);
  await expect(setup.getByRole('alert')).toContainText('This page could not be loaded.');
  await expect(setup.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
  await expect(setup.getByRole('button', { name: 'Reload application' })).toBeVisible();
  await expect(setup.getByRole('button', { name: 'Close dialog' })).toBeVisible();
  await setup.getByRole('button', { name: 'Close dialog' }).click();
  await expect(dialog(page)).toHaveCount(0);
});

test('cancelling after an import failure and reopening loads the form', async ({ page }) => {
  let fail = true;
  await page.route(formModule, (route) => (fail ? route.abort() : route.continue()));
  const setup = await openSetup(page);
  await expect(setup.getByRole('alert')).toBeVisible();
  await setup.getByRole('button', { name: 'Close dialog' }).click();
  await expect(dialog(page)).toHaveCount(0);
  fail = false;
  await page.getByRole('button', { name: 'Play vs AI', exact: true }).click();
  const reopened = dialog(page);
  await expect(reopened.getByRole('button', { name: 'To the table' })).toBeVisible();
});

test('loads the form and creates a game through the normal submission', async ({ page }) => {
  const setup = await openSetup(page);
  await expect(setup.getByRole('button', { name: 'To the table' })).toBeVisible();
  const created = page.waitForResponse(
    (response) => response.url().endsWith('/api/games') && response.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'To the table' }).click();
  expect((await created).ok()).toBe(true);
  await expect(page.getByRole('grid')).toBeVisible();
});

test('navigation during a pending import does not reveal the dialog on another route', async ({
  page,
}) => {
  const { release, loaded } = await holdFormImport(page);
  const setup = await openSetup(page);
  await page.evaluate(() => {
    history.pushState(null, '', '/statistics');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.locator('.statistics-page')).toBeVisible();
  release();
  await loaded;
  await expect(page.getByRole('dialog', { name: 'Begin a new saga', exact: true })).toHaveCount(0);
});

test('query-driven setup preserves the selected ruleset', async ({ page }) => {
  const setup = await openSetup(page, '?setup=single-player&ruleset=brandubh');
  await expect(setup.getByRole('radio', { name: /Brandubh/ })).toBeChecked();
});

test('restores focus to the opener after the dialog closes', async ({ page }) => {
  await page.goto('/dashboard');
  const opener = page.getByRole('button', { name: 'Play vs AI', exact: true });
  await opener.click();
  const setup = dialog(page);
  await expect(setup.getByRole('button', { name: 'To the table' })).toBeVisible();
  await setup.getByRole('button', { name: 'Close dialog' }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(opener).toBeFocused();
});

for (const language of ['en', 'de'] as const) {
  test(`shows ${language} loading and failure feedback inside the modal`, async ({ page }) => {
    await page.addInitScript(
      (value) => localStorage.setItem('hnefatafl-language', value),
      language,
    );
    const german = language === 'de';
    await page.route(formModule, (route) => route.abort());
    const setup = await openSetup(
      page,
      '?setup=single-player',
      german ? 'Beginne eine neue Saga' : 'Begin a new saga',
    );
    await expect(setup).toBeVisible();
    await expect(setup.getByRole('alert')).toContainText(
      german ? 'Diese Seite konnte nicht geladen werden.' : 'This page could not be loaded.',
    );
    await expect(
      setup.getByRole('button', { name: german ? 'Erneut versuchen' : 'Retry', exact: true }),
    ).toBeVisible();
  });
}
