import { expect, type Page } from '@playwright/test';

export async function openGameOptions(page: Page) {
  if (/^\/(games|online\/games)\//.test(new URL(page.url()).pathname))
    await expect(page.getByRole('grid')).toBeVisible();
  if (
    !(await page
      .getByRole('button', { name: /Game options|Spieloptionen/, exact: true })
      .isVisible())
  ) {
    await gameTab(page, 'analytics');
    return;
  }
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

export async function gameTab(page: Page, tab: 'moves' | 'analytics' | 'games') {
  if (/^\/(games|online\/games)\//.test(new URL(page.url()).pathname))
    await expect(page.getByRole('grid')).toBeVisible();
  await closeGameOptions(page);
  const names = {
    moves: /^(Moves|Züge) \d+$/,
    analytics: /^(Analytics|Analyse)$/,
    games: /^(My games|Meine Partien)$/,
  };
  const button = page
    .locator('.mobile-inspector, .desktop-inspector')
    .getByRole('tab', { name: names[tab] });
  if (await button.isVisible()) await button.click();
}

export async function showMove(page: Page, ply: number) {
  await gameTab(page, 'moves');
  const row = page.getByRole('button', { name: `Replay to move ${ply}`, exact: true });
  await row.scrollIntoViewIfNeeded();
  await expect(row).toBeVisible();
}

export async function openGameNavigation(page: Page) {
  const play = /^\/(games|online\/games)\//.test(new URL(page.url()).pathname);
  if (play) await expect(page.getByRole('grid')).toBeVisible();
  await closeGameOptions(page);
  const back = page.locator('.mobile-play-header a');
  if (await back.isVisible()) {
    await back.click();
  } else if (play) {
    await page.getByRole('button', { name: /^(Dashboard|Übersicht)$/ }).click();
  }
  if (play) {
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole('combobox', { name: /^(Language|Sprache)$/ })).toBeVisible();
  }
}

export async function returnToGame(page: Page, path: string) {
  if (new URL(page.url()).pathname !== path) {
    await page.goto(path);
    await expect(page.getByRole('grid')).toBeVisible();
  }
}

export async function openMyGames(page: Page) {
  const compact = await page.evaluate(
    () => matchMedia('(max-width: 760px), (max-height: 480px) and (pointer: coarse)').matches,
  );
  if (compact) {
    await expect(page.locator('.mobile-game-layout')).toBeVisible();
  }
  await gameTab(page, 'games');
}
