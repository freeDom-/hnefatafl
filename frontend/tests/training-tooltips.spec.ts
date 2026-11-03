import { expect, test, type Page } from './auth-fixture';
import { openCollection, openCreation } from './workshop-helpers';

const api = 'http://127.0.0.1:8001/api';

async function openWorkshop(page: Page) {
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
}

/**
 * The parameter help must work with a real pointer, a real keyboard and a real
 * touch tap. jsdom cannot reproduce pointer/focus sequencing, so these checks run
 * in Chromium against the served application.
 */
test('parameter tooltips follow pointer, click and keyboard in the new-job form', async ({
  page,
}) => {
  await openWorkshop(page);
  await openCreation(page);

  const label = page.getByText('Training iterations', { exact: true });
  const icon = page.getByRole('button', { name: 'Explain Training iterations', exact: true });
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toHaveCount(0);

  // Hovering the icon on its own reveals the description.
  await icon.hover();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Number of training iterations');
  // The symbol itself stays small, so the field row keeps its height.
  const symbol = (await icon.boundingBox())!;
  expect(symbol.width).toBeLessThanOrEqual(20);
  expect(symbol.height).toBeLessThanOrEqual(20);

  // Moving between the label and the icon must not flicker or dismiss it.
  await label.hover();
  await expect(tooltip).toBeVisible();
  await icon.hover();
  await expect(tooltip).toBeVisible();

  // Leaving the group hides a hovered tooltip again.
  await page.mouse.move(4, 4);
  await expect(tooltip).toHaveCount(0);

  // A click pins it, and a second click closes it although the icon keeps focus.
  await icon.click();
  await expect(icon).toBeFocused();
  await expect(tooltip).toBeVisible();
  await icon.click();
  await expect(icon).toBeFocused();
  await expect(tooltip).toHaveCount(0);

  // Escape dismisses it, Enter reopens it without leaving the icon, and the
  // tooltip never steals focus from its trigger.
  await icon.click();
  await expect(tooltip).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(tooltip).toHaveCount(0);
  await expect(icon).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(tooltip).toBeVisible();
  await expect(icon).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(tooltip).toHaveCount(0);

  // The parameter itself is still editable while the help is around.
  await icon.click();
  await page.getByLabel('Training iterations', { exact: true }).fill('7');
  await expect(page.getByLabel('Training iterations', { exact: true })).toHaveValue('7');
  await page.mouse.move(4, 4);
});

test('parameter tooltips follow a touch tap', async ({ page }, info) => {
  test.skip(info.project.name !== 'mobile', 'touch input is only exercised by the mobile project');
  await openWorkshop(page);
  await openCreation(page);

  const icon = page.getByRole('button', { name: 'Explain Network channels', exact: true });
  const tooltip = page.getByRole('tooltip');
  await icon.tap();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Feature width of the network');
  await icon.tap();
  await expect(tooltip).toHaveCount(0);

  // The visible symbol stays small, but the coarse-pointer target around it is
  // large enough to tap: a point below the symbol still belongs to the button.
  await icon.scrollIntoViewIfNeeded();
  const box = (await icon.boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(20);
  expect(box.height).toBeLessThanOrEqual(20);
  const x = box.x + box.width / 2;
  const y = box.y + box.height + 8;
  const owner = await page.evaluate(
    ([px, py]) => document.elementFromPoint(px, py)?.closest('.field-info-button') !== null,
    [x, y],
  );
  expect(owner).toBe(true);
  await page.touchscreen.tap(x, y);
  await expect(tooltip).toBeVisible();
});

test('a resume-dialog tooltip renders inside the modal and Escape keeps it open', async ({
  page,
  request,
}, info) => {
  const name = `Tooltip raven ${info.project.name}`;
  const started = await request.post(`${api}/training/jobs`, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      device: 'cpu',
      cpu_threads: 1,
      model_name: name,
      max_game_plies: 2,
      evaluation_max_plies: 2,
    },
  });
  const created = await started.text();
  expect(started.ok(), created).toBe(true);
  const source = JSON.parse(created);
  await expect
    .poll(
      async () => (await (await request.get(`${api}/training/jobs/${source.id}`)).json()).status,
      {
        timeout: 60000,
      },
    )
    .toBe('completed');

  await openWorkshop(page);
  await openCollection(page);
  await page.getByRole('button', { name: `Resume training for ${name}`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Resume training' });
  await expect(dialog).toBeVisible();

  const icon = dialog.getByRole('button', {
    name: 'Explain Target total iterations',
    exact: true,
  });
  // A touch tap reaches the same button inside the dialog on a touch device.
  if (info.project.name === 'mobile') await icon.tap();
  else await icon.click();
  // The tooltip is portaled into the dialog, so the modal never clips it.
  const tooltip = dialog.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Number of training iterations');

  // The first Escape only dismisses the tooltip; the dialog stays open and a
  // second Escape still closes it.
  await page.keyboard.press('Escape');
  await expect(tooltip).toHaveCount(0);
  await expect(dialog).toBeVisible();

  // The immutable checkpoint summary carries the same help. The combined
  // architecture row exposes two independently named buttons and the tooltip
  // still stays inside the modal, including on a small viewport.
  const channels = dialog.getByRole('button', {
    name: 'Explain Network channels',
    exact: true,
  });
  const blocks = dialog.getByRole('button', {
    name: 'Explain Residual blocks',
    exact: true,
  });
  await expect(channels).toBeVisible();
  await expect(blocks).toBeVisible();
  if (info.project.name === 'mobile') await channels.tap();
  else await channels.click();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Feature width of the network');
  if (info.project.name === 'mobile') {
    const box = await tooltip.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
  }
  await page.keyboard.press('Escape');
  await expect(tooltip).toHaveCount(0);
  if (info.project.name === 'mobile') await blocks.tap();
  else await blocks.click();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Number of residual blocks');
  await page.keyboard.press('Escape');
  await expect(tooltip).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('a long German description stays inside a small viewport', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 420 });
  await openWorkshop(page);
  await openCreation(page);
  await page.getByLabel('Language', { exact: true }).selectOption('de');

  const icon = page.getByRole('button', { name: 'Inferenzcache erklären', exact: true });
  await icon.click();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('Verwendet neuronale Auswertungen identischer Stellungen');
  const box = await tooltip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(360);
  expect(box!.y + box!.height).toBeLessThanOrEqual(420);
});
