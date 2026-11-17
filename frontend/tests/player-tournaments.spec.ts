import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from './auth-fixture';
import type {
  PlayerTournamentBracket,
  PlayerTournamentDetail,
  TournamentMatch,
  TournamentPlayer,
} from '../src/playerTournamentTypes';

const identifier = `ptournament-${'b'.repeat(32)}`;
function fixture(owner: string) {
  const players: TournamentPlayer[] = [
    { account_id: owner, name: 'Archer', portrait_id: 'shieldbearer' },
    { account_id: 'keeper', name: 'Keeper', portrait_id: 'shieldmaiden' },
    { account_id: 'skald', name: 'Skald', portrait_id: 'skald' },
    { account_id: 'jarl', name: 'Jarl', portrait_id: 'iron-helm' },
  ];
  const match = (
    id: string,
    bracket: TournamentMatch['bracket'],
    round: number,
    source_a: string,
    source_b: string,
  ): TournamentMatch => ({
    id,
    bracket,
    round,
    slot: 0,
    source_a,
    source_b,
    status: 'pending',
    participants: { a: null, b: null },
    winner: null,
    loser: null,
    legs: [],
    decided_by: null,
    created_at: '2035-01-01T00:00:00Z',
    settled_at: null,
  });
  const first = {
    ...match('u1', 'upper', 1, 'seed:1', 'seed:4'),
    status: 'complete' as const,
    participants: { a: players[0], b: players[3] },
    winner: players[0],
    loser: players[3],
    decided_by: 'play' as const,
  };
  const second = {
    ...match('u2', 'upper', 1, 'seed:2', 'seed:3'),
    slot: 1,
    status: 'ready' as const,
    participants: { a: players[1], b: players[2] },
    legs: [
      {
        leg: 1,
        game_id: `online-${'a'.repeat(32)}`,
        game_status: 'active',
        outcome: null,
        result_revision: null,
        settled_at: null,
      },
    ],
  };
  const tournament: PlayerTournamentDetail = {
    id: identifier,
    name: 'Fjord championship',
    ruleset: 'brandubh',
    format: 'double_elimination',
    status: 'running',
    review_state: 'ok',
    capacity: 4,
    match_legs: 1,
    time_per_move: 3600,
    bracket_size: 4,
    entrant_count: 4,
    version: 7,
    created_at: '2035-01-01T00:00:00Z',
    started_at: '2035-02-01T00:00:00Z',
    completed_at: null,
    registration_deadline: '2035-02-01T00:00:00Z',
    scheduled_start_at: '2035-02-02T00:00:00Z',
    official_winner: null,
    entrants: players.map((player, index) => ({
      ...player,
      seed: index + 1,
      losses: 0,
      eliminated: false,
      registered_at: '2035-01-01T00:00:00Z',
    })),
    match_counts: { ready: 1, complete: 1, pending: 4 },
  };
  const bracket: PlayerTournamentBracket = {
    tournament_id: identifier,
    version: 7,
    status: 'running',
    review_state: 'ok',
    bracket_size: 4,
    upper: [
      { round: 1, matches: [first, second] },
      { round: 2, matches: [match('u3', 'upper', 2, 'winner:u1', 'winner:u2')] },
    ],
    lower: [
      { round: 1, matches: [match('l1', 'lower', 1, 'loser:u1', 'loser:u2')] },
      { round: 2, matches: [match('l2', 'lower', 2, 'winner:l1', 'loser:u3')] },
    ],
    final: match('final', 'grand_final', 1, 'winner:u3', 'winner:l2'),
    reset: null,
  };
  return { tournament, bracket };
}
async function mockTournament(page: Page, owner: string) {
  const state = fixture(owner);
  await page.route('**/api/player-tournaments**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET')
      return route.fulfill({
        status: 409,
        json: { detail: 'This tournament changed. Reload before retrying.' },
      });
    if (path.endsWith('/bracket')) return route.fulfill({ json: state.bracket });
    if (path === `/api/player-tournaments/${identifier}`)
      return route.fulfill({ json: state.tournament });
    return route.fulfill({ json: { items: [state.tournament], next_cursor: null, limit: 20 } });
  });
  return state;
}
function contrast(foreground: string, background: string) {
  const luminance = (color: string) => {
    const [r, g, b] = color
      .match(/[\d.]+/g)!
      .slice(0, 3)
      .map(Number)
      .map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
    return r * 0.2126 + g * 0.7152 + b * 0.0722;
  };
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

for (const theme of ['kings-table', 'iron-ash', 'saga-hall', 'classic-table']) {
  test(`human tournament bracket is hierarchical, responsive and readable in ${theme}`, async ({
    page,
    adminSession,
  }, info) => {
    await mockTournament(page, adminSession.id);
    const sizes =
      info.project.name === 'mobile'
        ? [
            { width: 320, height: 640 },
            { width: 390, height: 844 },
          ]
        : [
            { width: 1920, height: 1080 },
            { width: 3840, height: 2160 },
          ];
    mkdirSync('test-results/player-tournaments', { recursive: true });
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        ({ theme, language }) => {
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme }),
          );
          localStorage.setItem('hnefatafl-language', language);
        },
        { theme, language },
      );
      for (const size of sizes) {
        await page.setViewportSize(size);
        await page.goto(`/player-tournaments/${identifier}`);
        await expect(page.getByRole('heading', { name: 'Fjord championship' })).toBeVisible();
        await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
        const region = page.locator('.player-bracket-scroll');
        await expect(region).toHaveAttribute('tabindex', '0');
        await expect(region.locator('.player-match')).toHaveCount(6);
        await expect(region.locator('svg path')).toHaveCount(8);
        expect(await region.locator('svg path').first().getAttribute('d')).toMatch(
          /M [\d.]+ [\d.]+ H/,
        );
        const geometry = await region.evaluate((node) => {
          const upper = node.querySelector('.player-bracket-upper')!.getBoundingClientRect();
          const lower = node.querySelector('.player-bracket-lower')!.getBoundingClientRect();
          const rounds = node.querySelectorAll('.player-bracket-upper .player-bracket-round');
          return {
            upperY: upper.y,
            lowerY: lower.y,
            firstX: rounds[0].getBoundingClientRect().x,
            secondX: rounds[1].getBoundingClientRect().x,
          };
        });
        expect(geometry.lowerY).toBeGreaterThan(geometry.upperY);
        expect(geometry.secondX).toBeGreaterThan(geometry.firstX);
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        ).toBe(true);
        const card = region.locator('.player-match').first();
        const colors = await card.evaluate((node) => ({
          foreground: getComputedStyle(node.querySelector('p')!).color,
          background: getComputedStyle(node).backgroundColor,
        }));
        expect(contrast(colors.foreground, colors.background)).toBeGreaterThanOrEqual(4.5);
        if (size.width < 1000) {
          expect(await region.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
          await region.focus();
          await page.keyboard.press('ArrowRight');
          await expect.poll(() => region.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
          await region.evaluate((node) => {
            node.scrollLeft = 0;
          });
        }
        await expect(async () => {
          await page.locator('#player-tournaments-heading').scrollIntoViewIfNeeded();
          await expect(page.locator('#player-tournaments-heading')).toBeVisible();
        }).toPass({ timeout: 10000 });
        await page.screenshot({
          path: `test-results/player-tournaments/${theme}-${language}-${size.width}.png`,
          fullPage: true,
        });
        await page.locator('.player-bracket-mode button').nth(1).click();
        await expect(page.locator('.player-bracket-list .player-match')).toHaveCount(6);
        await expect(page.locator('.player-bracket-scroll')).toHaveCount(0);
        await expect(page.locator('.player-bracket-list a')).toHaveAttribute(
          'href',
          `/online/games/online-${'a'.repeat(32)}`,
        );
      }
    }
  });
}

test('administrator creates a real tournament; registration and withdrawal persist through the authoritative API', async ({
  page,
  request,
}, info) => {
  let id = '';
  try {
    await page.goto('/player-tournaments');
    await page.getByRole('button', { name: 'Create player tournament' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Tournament name').fill(`Browser cup ${info.project.name}`);
    await dialog.getByLabel('Maximum entrants').fill('4');
    await dialog.getByLabel('Days', { exact: true }).fill('0');
    await dialog.getByLabel('Hours', { exact: true }).fill('1');
    await dialog.getByRole('button', { name: 'Create player tournament', exact: true }).click();
    await expect(page).toHaveURL(/\/player-tournaments\/ptournament-[0-9a-f]{32}$/);
    id = page.url().split('/').at(-1)!;
    await page.getByRole('button', { name: 'Register for tournament', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Register for tournament', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Withdraw registration', exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Withdraw registration', exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Withdraw registration', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Withdraw registration', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Register for tournament', exact: true }),
    ).toBeVisible();
    const authoritative = await (
      await request.get(`http://127.0.0.1:8001/api/player-tournaments/${id}`)
    ).json();
    expect(authoritative.entrants).toHaveLength(0);
    expect(authoritative.status).toBe('registration');
  } finally {
    if (id) {
      const current = await (
        await request.get(`http://127.0.0.1:8001/api/player-tournaments/${id}`)
      ).json();
      expect(
        (
          await request.post(`http://127.0.0.1:8001/api/player-tournaments/${id}/cancel`, {
            data: { version: current.version },
          })
        ).ok(),
      ).toBe(true);
    }
  }
});

test('ordinary players see competition data without administrative controls', async ({
  page,
  adminSession,
}) => {
  const state = await mockTournament(page, adminSession.id);
  state.tournament.status = 'registration';
  await page.route('**/api/auth/me', async (route) => {
    const response = await route.fetch();
    const session = await response.json();
    await route.fulfill({
      json: {
        ...session,
        account: { ...session.account, role: 'USER', permissions: ['play', 'profile'] },
      },
    });
  });
  await page.goto(`/player-tournaments/${identifier}`);
  await expect(page.getByRole('heading', { name: 'Fjord championship' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Withdraw registration', exact: true }),
  ).toBeVisible();
  for (const name of [
    'Create player tournament',
    'Start tournament',
    'Cancel tournament',
    'Rebuild bracket',
    'Override match result',
  ])
    await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
});

test('moderation failure retains the confirmation and announced error instead of changing a winner locally', async ({
  page,
  adminSession,
}) => {
  await mockTournament(page, adminSession.id);
  await page.goto(`/player-tournaments/${identifier}`);
  await page.getByRole('button', { name: 'Override match result', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Private administrator reason').fill('Reviewed official result revision');
  await dialog.getByRole('button', { name: 'Override match result', exact: true }).click();
  await expect(dialog.getByRole('alert')).toHaveText(
    'This tournament changed. Reload before retrying.',
  );
  await expect(dialog).toBeVisible();
  await expect(page.locator('.player-tournament-champion')).toHaveCount(0);
});

for (const theme of ['classic-table', 'kings-table', 'iron-ash', 'saga-hall']) {
  test(`tournament calendar supports keyboard input and theme ${theme}`, async ({ page }, info) => {
    await page
      .context()
      .addInitScript(
        (theme) =>
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme }),
          ),
        theme,
      );
    const sizes =
      info.project.name === 'mobile'
        ? [
            { width: 320, height: 640 },
            { width: 390, height: 844 },
          ]
        : [
            { width: 1920, height: 1080 },
            { width: 3840, height: 2160 },
          ];
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        (language) => localStorage.setItem('hnefatafl-language', language),
        language,
      );
      for (const size of sizes) {
        await page.setViewportSize(size);
        await page.goto('/player-tournaments');
        const createName =
          language === 'en' ? 'Create player tournament' : 'Spielerturnier erstellen';
        const opener = page.getByRole('button', { name: createName, exact: true });
        await opener.click();
        const dialog = page.getByRole('dialog');
        const picker = dialog.getByRole('group', {
          name:
            language === 'en'
              ? 'Planned start (your local time, optional)'
              : 'Geplanter Beginn (deine Ortszeit, optional)',
        });
        const date = picker.getByRole('textbox', {
          name: language === 'en' ? 'Date (YYYY-MM-DD)' : 'Datum (JJJJ-MM-TT)',
        });
        await date.fill('2035-02-01');
        const toggle = picker.locator('summary');
        await toggle.focus();
        await page.keyboard.press('Enter');
        const selected = picker.locator('[aria-selected=true] button');
        await expect(selected).toHaveCount(1);
        await selected.focus();
        await expect(selected).toBeFocused();
        await page.keyboard.press('ArrowRight');
        await page.keyboard.press('Enter');
        await expect(date).toHaveValue('2035-02-02');
        const next = picker.getByRole('button', {
          name: language === 'en' ? 'Next month' : 'Nächster Monat',
        });
        await next.click();
        await expect(picker.getByRole('grid')).toBeVisible();
        const calendar = picker.locator('.themed-calendar');
        const colors = await calendar.evaluate((node) => ({
          background: getComputedStyle(node).backgroundColor,
          color: getComputedStyle(node).color,
        }));
        expect(contrast(colors.color, colors.background)).toBeGreaterThanOrEqual(4.5);
        const dimensions = await dialog.evaluate((node) => ({
          client: node.clientWidth,
          scroll: node.scrollWidth,
        }));
        expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
        await page.keyboard.press('Tab');
        await expect(dialog).toBeVisible();
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
        if (language === 'en') {
          mkdirSync('test-results/player-tournaments-calendar', { recursive: true });
          await dialog.screenshot({
            path: `test-results/player-tournaments-calendar/${theme}-${size.width}.png`,
          });
        }
        await page.keyboard.press('Escape');
        await expect(dialog).not.toBeVisible();
        await expect(opener).toBeFocused();
      }
    }
  });
}

test('Berlin DST gap is rejected and both repeated-hour instants serialize correctly', async ({
  browser,
  adminSession,
}) => {
  const context = await browser.newContext({
    storageState: adminSession.state,
    timezoneId: 'Europe/Berlin',
  });
  const page = await context.newPage();
  await page.goto('/player-tournaments');
  await page.getByRole('button', { name: 'Create player tournament' }).click();
  const picker = page.getByRole('group', { name: 'Planned start (your local time, optional)' });
  await picker.getByLabel('Date (YYYY-MM-DD)').fill('2035-03-25');
  await picker.getByLabel('Time (HH:MM)').fill('02:30');
  await expect(picker.getByRole('alert')).toContainText('does not exist');
  await expect(picker.getByLabel('Date (YYYY-MM-DD)')).toHaveAttribute('aria-invalid', 'true');
  await picker.getByLabel('Date (YYYY-MM-DD)').fill('2035-10-28');
  await expect(picker.getByRole('alert')).toHaveCount(0);
  const occurrence = picker.getByRole('combobox');
  await expect(occurrence).toBeVisible();
  await expect(picker.locator('input[name=scheduled_start_at]')).toHaveValue(
    '2035-10-28T00:30:00.000Z',
  );
  await occurrence.selectOption('1');
  await expect(picker.locator('input[name=scheduled_start_at]')).toHaveValue(
    '2035-10-28T01:30:00.000Z',
  );
  await context.close();
});
