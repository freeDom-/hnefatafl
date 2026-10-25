import { expect, test } from './auth-fixture';

test('a failed new-game creation keeps the snackbar visible above the scrolled dialog', async ({
  page,
}) => {
  await page.route('**/api/games', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'The table is full.' }),
    });
  });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Begin a new saga', exact: true });
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByRole('button', { name: 'AI vs AI', exact: true }).click();

  // Scroll the submit control into view: this is the position where the old
  // top-of-page banner was no longer visible.
  const submit = setup.getByRole('button', { name: /To the table/ });
  await submit.scrollIntoViewIfNeeded();
  await submit.click();

  const snackbar = page.getByRole('alert').filter({ hasText: 'The table is full.' });
  await expect(snackbar).toBeVisible();
  const box = await snackbar.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport!.height);
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width);

  // The notification sits in the top layer, so the point at its centre belongs
  // to the snackbar rather than the modal dialog or its backdrop.
  const overDialog = await page.evaluate(
    ({ x, y }) => Boolean(document.elementFromPoint(x, y)?.closest('.notification-viewport')),
    { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 },
  );
  expect(overDialog).toBe(true);

  // Selections survive the failure so the user can retry.
  await expect(setup.getByRole('button', { name: 'AI vs AI', exact: true })).toHaveClass('active');

  // Dismissal removes the notification.
  await page.getByRole('button', { name: 'Dismiss notification' }).click();
  await expect(snackbar).toHaveCount(0);
});
