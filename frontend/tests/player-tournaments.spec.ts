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
    return_game_state: 'waiting_for_game' as const,
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
    match_legs: 2,
    match_mode: 'sequential',
    time_per_move: 3600,
    bracket_size: 4,
    entrant_count: 4,
    version: 7,
    created_at: '2035-01-01T00:00:00Z',
    started_at: '2035-02-01T00:00:00Z',
    completed_at: null,
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
    round_progress: { number: 1, finished_matches: 1, outstanding_matches: 1 },
    upper: [
      { round: 1, matches: [first, second] },
      {
        round: 2,
        matches: [
          { ...match('u3', 'upper', 2, 'winner:u1', 'winner:u2'), waiting_for_round: true },
        ],
      },
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
        await expect(
          page.getByText(
            language === 'en'
              ? 'Tournament round 1 · 1 finished · 1 outstanding'
              : 'Turnierrunde 1 · 1 entschieden · 1 offen',
          ),
        ).toBeVisible();
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

for (const theme of ['classic-table', 'kings-table', 'iron-ash', 'saga-hall']) {
  test(`maximum tournament preview scrolls within the page in ${theme}`, async ({
    page,
    request,
  }, info) => {
    const response = await request.post('http://127.0.0.1:8001/api/player-tournaments', {
      data: { name: 'Open championship', capacity: 64, match_mode: 'sequential' },
    });
    expect(response.status()).toBe(201);
    const { id } = await response.json();
    await page.addInitScript((theme) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ version: 2, uiTheme: theme }));
      localStorage.setItem('hnefatafl-language', 'en');
    }, theme);
    const mobile = info.project.name === 'mobile';
    for (const width of mobile ? [320, 390] : [1920, 3840]) {
      await page.setViewportSize({ width, height: mobile ? 844 : width === 1920 ? 1080 : 2160 });
      await page.goto(`/player-tournaments/${id}`);
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
      await expect(page.getByText(/Provisional bracket based on maximum entrants/)).toBeVisible();
      const region = page.getByRole('region', { name: 'Double-elimination bracket' });
      await expect(region.getByRole('article')).toHaveCount(126);
      await expect(region.getByText('Unassigned seed 64', { exact: true })).toHaveCount(1);
      await expect(region.getByRole('link', { name: /^Game \d/ })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        width + 1,
      );
      expect(await region.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
      await region.focus();
      await page.keyboard.press('ArrowRight');
      await expect.poll(() => region.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
      if (width === 390 || width === 1920) {
        await region.evaluate((node) => {
          node.scrollLeft = 0;
        });
        await page.locator('#player-bracket-heading').evaluate((node) => node.scrollIntoView());
        mkdirSync('test-results/player-tournaments', { recursive: true });
        await page.screenshot({
          path: `test-results/player-tournaments/${theme}-preview-${width}.png`,
        });
      }
      await page.getByRole('button', { name: 'Accessible match list' }).click();
      await expect(page.locator('.player-bracket-list').getByRole('article')).toHaveCount(126);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        width + 1,
      );
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
    // Each case checks the planned start in two languages and two viewport sizes.
    test.setTimeout(180000);
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
        const dialog = page.getByRole('dialog', { name: createName, exact: true });
        const picker = dialog.getByRole('group', {
          name:
            language === 'en'
              ? 'Planned start (your local time, optional)'
              : 'Geplanter Beginn (deine Ortszeit, optional)',
        });
        const date = picker.getByRole('textbox', {
          name: language === 'en' ? 'Date (YYYY-MM-DD)' : 'Datum (JJJJ-MM-TT)',
        });
        const time = picker.getByRole('textbox', {
          name: language === 'en' ? 'Time (HH:MM)' : 'Uhrzeit (HH:MM)',
        });
        const toggle = picker.getByRole('button', {
          name: language === 'en' ? 'Open calendar' : 'Kalender öffnen',
        });
        const clear = picker.getByRole('button', {
          name: language === 'en' ? 'Clear date' : 'Datum löschen',
        });
        await expect(clear).toHaveCount(0);
        await expect(
          picker.getByRole('button', {
            name: language === 'en' ? 'Choose date' : 'Datum wählen',
            exact: true,
          }),
        ).toHaveCount(0);
        await date.fill('');
        await date.pressSequentially('20350201');
        await time.fill('');
        await time.pressSequentially('1830');
        await expect(date).toHaveValue('2035-02-01');
        await expect(time).toHaveValue('18:30');
        // Real-browser check for caret-aware middle editing and separator deletion.
        await date.fill('2035-02-01');
        await date.evaluate((node) => (node as HTMLInputElement).setSelectionRange(5, 7));
        await page.keyboard.type('12');
        await expect(date).toHaveValue('2035-12-01');
        await date.evaluate((node) => (node as HTMLInputElement).setSelectionRange(5, 5));
        await page.keyboard.press('Backspace');
        await expect(date).toHaveValue('203-12-01');
        await page.keyboard.type('5');
        await expect(date).toHaveValue('2035-12-01');
        await date.fill('2035-02-01');
        await date.scrollIntoViewIfNeeded();
        const before = await dialog.evaluate((node) => ({
          height: node.getBoundingClientRect().height,
          scroll: node.scrollHeight,
        }));
        if (info.project.name === 'mobile') {
          // Touching the field starts manual entry instead of covering it with the popup.
          await date.click();
          await expect(picker.getByRole('dialog')).toHaveCount(0);
          await date.press('ArrowDown');
        } else {
          await date.click();
        }
        const popup = picker.getByRole('dialog');
        await expect(popup).toBeVisible();
        if (info.project.name === 'mobile') {
          await expect(popup.locator('[aria-selected=true] button')).toBeFocused();
        } else {
          await expect(date).toBeFocused();
        }
        expect(await popup.evaluate((node) => !!node.closest('dialog[open]'))).toBe(true);
        const bounds = await popup.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.y).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(size.width + 1);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(size.height + 1);
        expect(
          await popup.evaluate((node) => {
            const rect = node.getBoundingClientRect();
            return node.contains(
              document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2),
            );
          }),
        ).toBe(true);
        expect(
          await dialog.evaluate((node) => ({
            height: node.getBoundingClientRect().height,
            scroll: node.scrollHeight,
          })),
        ).toEqual(before);
        const calendar = popup.locator('.themed-calendar');
        const colors = await calendar.evaluate((node) => ({
          background: getComputedStyle(node).backgroundColor,
          color: getComputedStyle(node).color,
        }));
        expect(contrast(colors.color, colors.background)).toBeGreaterThanOrEqual(4.5);
        await expect(popup.getByRole('grid')).toHaveAccessibleName(
          language === 'en' ? 'February 2035' : 'Februar 2035',
        );
        if (info.project.name !== 'mobile') {
          await page.keyboard.press('ArrowDown');
          await expect(popup.locator('[aria-selected=true] button')).toBeFocused();
        }
        await date.focus();
        await page.keyboard.press('ArrowDown');
        await expect(popup.locator('[aria-selected=true] button')).toBeFocused();
        await page.keyboard.press('ArrowRight');
        await page.keyboard.press('Enter');
        await expect(popup).toHaveCount(0);
        await expect(date).toHaveValue('2035-02-02');
        await expect(date).toBeFocused();
        await expect(time).toHaveValue('18:30');
        await toggle.click();
        await popup
          .getByRole('button', { name: language === 'en' ? 'Next month' : 'Nächster Monat' })
          .click();
        await expect(popup.getByRole('grid')).toHaveAccessibleName(
          language === 'en' ? 'March 2035' : 'März 2035',
        );
        await page.keyboard.press('Tab');
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
        if (language === 'en') {
          mkdirSync('test-results/player-tournaments-compact-calendar', { recursive: true });
          await page.screenshot({
            path: `test-results/player-tournaments-compact-calendar/${theme}-start-${size.width}.png`,
          });
        }
        await page.keyboard.press('Escape');
        await expect(popup).toHaveCount(0);
        await expect(dialog).toBeVisible();
        await expect(date).toBeFocused();
        await toggle.click();
        await time.click();
        await expect(popup).toHaveCount(0);
        await expect(time).toBeFocused();
        await expect(date).toHaveValue('2035-02-02');
        await clear.focus();
        await page.keyboard.press('Enter');
        await expect(date).toHaveValue('');
        await expect(date).toBeFocused();
        await expect(time).toHaveValue('18:30');
        expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(
          true,
        );
        await page.keyboard.press('Escape');
        await expect(dialog).not.toBeVisible();
        await expect(opener).toBeFocused();
      }
    }
  });
}

test('tournament page and calendar load only after navigating away from the dashboard', async ({
  page,
}) => {
  const modules: string[] = [];
  page.on('request', (request) => modules.push(request.url()));
  const tournamentModules = () =>
    modules.filter((url) =>
      /PlayerTournaments\.tsx|LocalDateTimePicker\.tsx|react-day-picker\.js/.test(url),
    );
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
  expect(tournamentModules()).toEqual([]);
  await page.goto('/player-tournaments');
  await expect(
    page.getByRole('heading', { name: 'Player tournaments', exact: true }),
  ).toBeVisible();
  expect(tournamentModules().some((url) => url.includes('PlayerTournaments.tsx'))).toBe(true);
  expect(tournamentModules().some((url) => url.includes('react-day-picker.js'))).toBe(true);
});

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
  await expect(picker.getByLabel('Time (HH:MM)')).toHaveAttribute('aria-invalid', 'true');
  expect(
    await page.locator('dialog form').evaluate((form) => (form as HTMLFormElement).checkValidity()),
  ).toBe(false);
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
  await picker.getByRole('button', { name: 'Open calendar' }).click();
  await picker.locator('[aria-selected=true] button').click();
  await expect(picker.getByRole('dialog')).toHaveCount(0);
  await expect(occurrence).toHaveValue('1');
  await expect(picker.locator('input[name=scheduled_start_at]')).toHaveValue(
    '2035-10-28T01:30:00.000Z',
  );
  await context.close();
});
