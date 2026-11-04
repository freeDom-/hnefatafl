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
 * Reads the tooltip rectangle and the visual viewport in the same layout-viewport
 * coordinate space. `getBoundingClientRect` and `visualViewport.offsetLeft/Top`
 * are both relative to the layout viewport, so the comparison proves the tooltip
 * fits what is actually visible under a page-scale (pinch-zoom style) change.
 */
async function visibleTooltip(page: Page) {
  return page.evaluate(() => {
    const element = document.querySelector<HTMLElement>('.field-tooltip')!;
    const rect = element.getBoundingClientRect();
    const view = window.visualViewport;
    const viewLeft = view?.offsetLeft ?? 0;
    const viewTop = view?.offsetTop ?? 0;
    return {
      width: rect.width,
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      viewLeft,
      viewTop,
      viewRight: viewLeft + (view?.width ?? window.innerWidth),
      viewBottom: viewTop + (view?.height ?? window.innerHeight),
    };
  });
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

test('a page-scale zoom narrows the tooltip to the visual viewport', async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 600 });
  await openWorkshop(page);
  await openCreation(page);

  const icon = page.getByRole('button', { name: 'Explain Training iterations', exact: true });
  await icon.click();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toHaveCSS('max-width', '300px');

  // Chromium's page scale factor produces a real visual viewport smaller than
  // the layout viewport — the same mechanism a pinch zoom uses — unlike
  // setViewportSize, which shrinks both. It is still an emulation: no physical
  // pinch gesture is performed. The pointer is parked in the corner first so the
  // zoom cannot slide a different field under it and open a second tooltip.
  await page.mouse.move(0, 0);
  const client = await context.newCDPSession(page);
  await client.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 });
  await expect(tooltip).toHaveCSS('max-width', '179px');

  const bounds = await visibleTooltip(page);
  // The 195px visual viewport minus both margins is 179px, and the box stays
  // inside the visible area instead of overflowing it.
  expect(bounds.width).toBeLessThanOrEqual(179.5);
  expect(bounds.left).toBeGreaterThanOrEqual(bounds.viewLeft - 0.5);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewRight + 0.5);
  expect(bounds.top).toBeGreaterThanOrEqual(bounds.viewTop - 0.5);
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
  context,
}, info) => {
  const name = `Tooltip raven ${info.project.name}`;
  const started = await request.post(`${api}/training/jobs`, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      device: 'cpu',
      cpu_threads: 1,
      model_name: name,
      // Distinct per project so the deterministic smoke run is not reused by the
      // other project's identically configured job.
      seed: info.project.name === 'mobile' ? 410001 : 410000,
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

  // A longer read-only summary description under a 200% page-scale zoom is taller
  // than the visible area, so it becomes a scrollable region inside the modal and
  // the end of the text stays reachable. Chromium auto-pans to the focused summary
  // button during the zoom, so the left edge is not asserted; the creation-form
  // test checks the rendered bounds.
  if (info.project.name === 'mobile') await channels.tap();
  else await channels.click();
  await expect(tooltip).toBeVisible();
  await page.mouse.move(0, 0);
  await page.setViewportSize({ width: 390, height: 600 });
  const client = await context.newCDPSession(page);
  await client.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 });
  const panel = dialog.getByRole('region', { name: 'Network channels' });
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS('overflow-y', 'auto');
  const metrics = await panel.evaluate((element) => ({
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  await panel.focus();
  await page.keyboard.press('End');
  await expect
    .poll(async () => panel.evaluate((el) => el.scrollTop + el.clientHeight))
    .toBeGreaterThanOrEqual(metrics.scrollHeight - 1);
  // Escape dismisses the panel first, returns focus to the summary button and
  // leaves the native dialog open.
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(channels).toBeFocused();
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('a long German description becomes a scrollable panel when zoomed', async ({
  page,
  context,
}) => {
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

  // A 200% page-scale zoom is a real visual-viewport change (not a physical pinch
  // gesture) that halves the visible area to 180 × 210. The long description no
  // longer fits, so it must become a scrollable region instead of a tooltip. The
  // pointer is parked in the corner so the zoom cannot open another field's help.
  await page.mouse.move(0, 0);
  const client = await context.newCDPSession(page);
  await client.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 });
  const panel = page.getByRole('region', { name: 'Inferenzcache' });
  await expect(panel).toBeVisible();
  // The button still points at the full description, so screen readers announce
  // the whole text even though the panel is scrolled.
  await expect(icon).toHaveAccessibleDescription(
    /Verwendet neuronale Auswertungen identischer Stellungen/,
  );
  await expect(panel).toHaveCSS('overflow-y', 'auto');
  await expect(panel).toHaveCSS('pointer-events', 'auto');
  const metrics = await panel.evaluate((element) => ({
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    maxHeight: getComputedStyle(element).maxHeight,
  }));
  // 210 − 2 × 8 = 194, and the full text is taller than the visible panel.
  expect(metrics.maxHeight).toBe('194px');
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);

  // The end of the description is reachable by mouse wheel.
  await panel.hover();
  await page.mouse.wheel(0, 10000);
  await expect
    .poll(async () => panel.evaluate((el) => el.scrollTop + el.clientHeight))
    .toBeGreaterThanOrEqual(metrics.scrollHeight - 1);

  // And by keyboard: focusing the region and pressing End scrolls to the end.
  await panel.focus();
  await page.keyboard.press('End');
  await expect
    .poll(async () => panel.evaluate((el) => el.scrollTop + el.clientHeight))
    .toBeGreaterThanOrEqual(metrics.scrollHeight - 1);

  // Escape dismisses the help, returns focus to the button and leaves the form.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('region', { name: 'Inferenzcache' })).toHaveCount(0);
  await expect(icon).toBeFocused();
  await expect(page.getByRole('combobox', { name: 'Inferenzcache' })).toBeVisible();
});

test('hovering into an oversized help panel keeps it open', async ({ page }) => {
  // A short viewport makes the long German description overflow even at scale 1,
  // so the tooltip becomes a scroll panel and its hover group can be exercised
  // without the page-scale emulation.
  await page.setViewportSize({ width: 360, height: 180 });
  await openWorkshop(page);
  await openCreation(page);
  await page.getByLabel('Language', { exact: true }).selectOption('de');

  const icon = page.getByRole('button', { name: 'Inferenzcache erklären', exact: true });
  await icon.hover();
  const panel = page.getByRole('region', { name: 'Inferenzcache' });
  await expect(panel).toBeVisible();
  // Moving from the icon onto the panel must keep it open so it can be scrolled.
  await panel.hover();
  await expect(panel).toBeVisible();
  // Leaving both closes it.
  await page.mouse.move(0, 0);
  await expect(panel).toHaveCount(0);
});
