import { expect, type Page } from '@playwright/test';

/**
 * Whether the shell collapses to its compact navigation at this viewport. The
 * breakpoint is shared by `siteHeader.css` (header navigation) and
 * `arenaShell.css` (bottom navigation), so both must agree on it.
 */
export async function isCompactShell(page: Page) {
  return page.evaluate(
    () => matchMedia('(max-width: 760px), (max-height: 480px) and (pointer: coarse)').matches,
  );
}

/** The single primary workspace navigation a viewport actually shows: the
 * compact bottom bar on phones, the full header navigation on tablets, and the
 * desktop rail on wide viewports. */
export function primaryNavigation(page: Page) {
  return page.locator(
    '.arena-sidebar nav:visible, .site-header nav:visible, .workspace-bottom-nav:visible',
  );
}

/**
 * Activate a workspace destination in whichever primary navigation the viewport
 * shows: the compact bottom bar on phones, the full header navigation otherwise.
 * Destinations carry their stable id, so this works in every language.
 */
export async function openDestination(page: Page, destination: string) {
  await primaryNavigation(page).locator(`[data-destination="${destination}"]`).click();
}

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
    // Language-independent: the compact header, the desktop rail and the bottom
    // bar all expose the same stable destination id.
    await primaryNavigation(page).locator('[data-destination="dashboard"]').click();
  }
  if (play) {
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole('combobox', { name: /^(Language|Sprache)$/ })).toBeVisible();
  }
}

/**
 * Enter the permission-gated Workshop through the shared workspace switch. The
 * Arena navigation deliberately exposes no Workshop destination, so the switch
 * is the only shell-level entry point.
 */
export async function openWorkshop(page: Page, destination?: string) {
  // The desktop rail exposes an "AI Workshop" link; the compact header keeps the
  // workspace switch button. Support both so the helper is shell-agnostic.
  const railEntry = page.getByRole('link', { name: /^(AI Workshop|KI-Werkstatt)$/ });
  if (await railEntry.isVisible().catch(() => false)) {
    await railEntry.click();
  } else {
    await page.getByRole('button', { name: /^(Workshop|Werkstatt)$/, exact: true }).click();
  }
  await expect(page).toHaveURL(/\/training$/);
  if (destination) await openDestination(page, destination);
}

/** Leave the Workshop for the Arena workspace in whichever shell is visible. */
export async function openArena(page: Page) {
  const railHome = page.locator('.arena-sidebar a[data-destination="dashboard"]');
  if (await railHome.isVisible().catch(() => false)) {
    await railHome.click();
  } else {
    await page.getByRole('button', { name: 'Arena', exact: true }).click();
  }
  await expect(page).toHaveURL(/\/dashboard/);
}

/** Start a new single-player game from whichever entry is visible: the dashboard
 * hero "Play vs AI" button or the Play card "New single-player game" button. */
export async function openNewSinglePlayer(page: Page) {
  await page
    .getByRole('button', { name: /^(Play vs AI|New single-player game)$/ })
    .first()
    .click();
}

export async function returnToGame(page: Page, path: string) {
  if (new URL(page.url()).pathname !== path) {
    await page.goto(path);
    await expect(page.getByRole('grid')).toBeVisible();
  }
}

export async function openMyGames(page: Page) {
  const compact = await isCompactShell(page);
  if (compact) {
    await expect(page.locator('.mobile-game-layout')).toBeVisible();
  }
  await gameTab(page, 'games');
}

/** Open the shared account profile without duplicating menu behavior in tests. */
export async function openProfile(page: Page) {
  await page.getByRole('button', { name: /^(Account menu for |Kontomenü für )/ }).click();
  await page.getByRole('menuitem', { name: /^(My Profile|Mein Profil)$/ }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole('heading', { name: /^(My Profile|Mein Profil)$/ })).toBeVisible();
}

export async function logoutFromAccount(page: Page) {
  await page.getByRole('button', { name: /^(Account menu for |Kontomenü für )/ }).click();
  await page.getByRole('menuitem', { name: /^(Log out|Abmelden)$/ }).click();
}
