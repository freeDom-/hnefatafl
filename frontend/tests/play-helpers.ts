import { expect, type Page } from '@playwright/test';

export async function openGameOptions(page: Page) {
  if (/^\/(games|online\/games)\//.test(new URL(page.url()).pathname))
    await expect(page.getByRole('grid')).toBeVisible();
  if (
    !(await page
      .getByRole('button', { name: /Game options|Spieloptionen/, exact: true })
      .isVisible())
  )
    return;
  const dialog = page.getByRole('dialog', { name: /Game options|Spieloptionen/, exact: true });
  if (!(await dialog.isVisible()))
    await page.getByRole('button', { name: /Game options|Spieloptionen/, exact: true }).click();
  await expect(dialog).toBeVisible();
}

export async function closeGameOptions(page: Page) {
  const dialog = page.getByRole('dialog', { name: /Game options|Spieloptionen/, exact: true });
  if (await dialog.isVisible())
    await dialog
      .getByRole('button', { name: /Close dialog|Dialog schließen/, exact: true })
      .click();
}

export async function gameTab(page: Page, tab: 'moves' | 'analytics' | 'players') {
  if (/^\/(games|online\/games)\//.test(new URL(page.url()).pathname))
    await expect(page.getByRole('grid')).toBeVisible();
  await closeGameOptions(page);
  const names = {
    moves: /^(Moves|Züge) \d+$/,
    analytics: /^(Analytics|Analyse)$/,
    players: /^(Players|Spieler)$/,
  };
  const button = page.locator('.mobile-inspector').getByRole('tab', { name: names[tab] });
  if (await button.isVisible()) await button.click();
}

export async function pauseGame(page: Page) {
  await openGameOptions(page);
  await page.getByRole('button', { name: /Pause AI/ }).click();
  await closeGameOptions(page);
}

export async function showMove(page: Page, ply: number) {
  await gameTab(page, 'moves');
  if (!(await page.locator('.mobile-inspector').isVisible())) return;
  const row = page.getByRole('button', { name: `Replay to move ${ply}`, exact: true });
  for (let pageNumber = 0; pageNumber < 200 && !(await row.isVisible()); pageNumber++) {
    const previous = page.getByRole('button', { name: 'Earlier moves', exact: true });
    if (!(await previous.isEnabled())) break;
    await previous.click();
  }
  await expect(row).toBeVisible();
}
