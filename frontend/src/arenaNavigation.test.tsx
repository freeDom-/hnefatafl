import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { ArenaShell, WorkshopShell } from './ArenaShell';
import type { Account } from './Auth';
import { I18nProvider, LANGUAGE_KEY, setLanguage } from './i18n';

const player: Account = {
  id: 'archer',
  username: 'Archer',
  email: 'archer@example.test',
  role: 'USER',
  permissions: ['play', 'profile'],
};
const trainer: Account = { ...player, permissions: ['play', 'profile', 'training'] };
const tester: Account = { ...player, permissions: ['play', 'profile', 'tournaments'] };
const keeper: Account = {
  ...player,
  permissions: ['play', 'profile', 'training', 'tournaments'],
};

beforeEach(() => {
  history.replaceState(null, '', '/dashboard');
  localStorage.clear();
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  history.replaceState(null, '', '/dashboard');
  setLanguage('en', false);
});

function shell(account: Account | null, workspace: 'arena' | 'workshop', current: string) {
  cleanup();
  const Shell = workspace === 'workshop' ? WorkshopShell : ArenaShell;
  return render(
    <I18nProvider>
      <Shell account={account} current={current}>
        <main id="main-content">Content</main>
      </Shell>
    </I18nProvider>,
  );
}

const headerNav = () => within(screen.getByRole('banner')).getByRole('navigation');
const headerLabels = () =>
  within(headerNav())
    .getAllByRole('button')
    .map((item) => item.textContent);
const bottomNav = (name = 'Mobile navigation') => screen.getByRole('navigation', { name });
const bottomLabels = (name?: string) =>
  within(bottomNav(name))
    .getAllByRole('link')
    .map((item) => item.textContent);

it('keeps Arena and Workshop destinations in separate workspace navigations', () => {
  shell(player, 'arena', 'dashboard');
  expect(headerLabels()).toEqual([
    'Dashboard',
    'Play',
    'Statistics',
    'Leaderboard',
    'Hall of records',
  ]);
  expect(within(headerNav()).queryByRole('button', { name: 'Training' })).toBeNull();
  expect(within(headerNav()).queryByRole('button', { name: 'AI testing' })).toBeNull();

  shell(keeper, 'workshop', 'training');
  expect(headerLabels()).toEqual(['Training', 'AI testing', 'Models', 'Workers']);
  expect(within(headerNav()).queryByRole('button', { name: 'Dashboard' })).toBeNull();
  expect(within(headerNav()).queryByRole('button', { name: 'Hall of records' })).toBeNull();
});

it('opens the Workshop switch only for permitted accounts and reaches an accessible destination', () => {
  shell(player, 'arena', 'dashboard');
  expect(screen.queryByRole('button', { name: 'Workshop' })).toBeNull();

  for (const [account, destination] of [
    [trainer, '/training'],
    [tester, '/tournaments'],
    [keeper, '/training'],
  ] as const) {
    shell(account, 'arena', 'dashboard');
    const entry = screen.getByRole('button', { name: 'Workshop' });
    expect(entry).toHaveAttribute('aria-pressed', 'false');
    expect(entry).toHaveAttribute('title', 'Workshop');
    fireEvent.click(entry);
    expect(location.pathname).toBe(destination);
    history.replaceState(null, '', '/dashboard');
  }
});

it('never shows an unauthorized Workshop destination', () => {
  shell(tester, 'workshop', 'tournaments');
  expect(headerLabels()).toEqual(['AI testing']);
  expect(bottomLabels()).toEqual(['Testing']);
  expect(within(headerNav()).queryByRole('button', { name: 'Models' })).toBeNull();

  shell(player, 'workshop', 'dashboard');
  expect(screen.queryByRole('navigation')).toBeNull();
});

it('uses the compact Workshop navigation instead of the Arena one on mobile', () => {
  shell(keeper, 'workshop', 'training');
  expect(bottomLabels()).toEqual(['Training', 'Testing', 'Models', 'Workers']);
  expect(within(bottomNav()).queryByRole('link', { name: 'Home' })).toBeNull();

  shell(player, 'arena', 'dashboard');
  expect(bottomLabels()).toEqual(['Home', 'Play', 'Stats', 'Compete', 'Replays']);
  expect(within(bottomNav()).queryByRole('link', { name: 'Training' })).toBeNull();
});

it('renders no navigation at all for signed-out visitors', () => {
  shell(null, 'arena', 'dashboard');
  expect(screen.queryByRole('navigation')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Workshop' })).toBeNull();
});

it('keeps the compact navigation outside the gameplay region so it cannot cover the board', () => {
  render(
    <I18nProvider>
      <ArenaShell account={keeper} current="play">
        <main id="main-content">
          <div className="mobile-game-layout">
            <div role="grid" aria-label="Board" />
            <section className="panel mobile-inspector">Moves</section>
          </div>
        </main>
      </ArenaShell>
    </I18nProvider>,
  );
  const main = screen.getByRole('main');
  expect(within(main).getByRole('grid', { name: 'Board' })).toBeVisible();
  const nav = bottomNav();
  expect(main.contains(nav)).toBe(false);
  expect(nav.previousElementSibling).toBe(main);
});

it('marks the active destination in both navigations, including workshop section anchors', () => {
  history.replaceState(null, '', '/play');
  shell(player, 'arena', 'play');
  expect(within(headerNav()).getByRole('button', { name: 'Play' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(bottomNav()).getByRole('link', { name: 'Play' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(bottomNav()).getByRole('link', { name: 'Home' })).not.toHaveAttribute(
    'aria-current',
  );

  history.replaceState(null, '', '/training#model-collection');
  shell(keeper, 'workshop', 'training');
  expect(within(headerNav()).getByRole('button', { name: 'Models' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(headerNav()).getByRole('button', { name: 'Training' })).not.toHaveAttribute(
    'aria-current',
  );
});

it('keeps every navigation control keyboard reachable with an accessible name', async () => {
  const user = userEvent.setup();
  shell(keeper, 'arena', 'dashboard');
  await user.tab();
  expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveFocus();
  const controls = [
    ...within(screen.getByRole('banner')).getAllByRole('button'),
    ...within(bottomNav()).getAllByRole('link'),
  ];
  expect(controls.length).toBeGreaterThan(5);
  for (const control of controls) {
    control.focus();
    expect(control).toHaveFocus();
    expect(control).toHaveAccessibleName();
  }
});

it('keeps the preserved routes reachable from the header navigation', () => {
  shell(keeper, 'arena', 'dashboard');
  for (const [label, path] of [
    ['Dashboard', '/dashboard'],
    ['Play', '/play'],
    ['Statistics', '/statistics'],
    ['Leaderboard', '/leaderboard'],
    ['Hall of records', '/archive'],
  ] as const) {
    fireEvent.click(within(headerNav()).getByRole('button', { name: label }));
    expect(location.pathname).toBe(path);
  }

  shell(keeper, 'workshop', 'training');
  for (const [label, path, hash] of [
    ['Training', '/training', ''],
    ['AI testing', '/tournaments', ''],
    ['Models', '/training', '#model-collection'],
    ['Workers', '/training', '#workshop-workers'],
  ] as const) {
    fireEvent.click(within(headerNav()).getByRole('button', { name: label }));
    expect(location.pathname).toBe(path);
    expect(location.hash).toBe(hash);
  }
});

it('localizes the Arena and Workshop navigation', () => {
  localStorage.setItem(LANGUAGE_KEY, 'de');
  shell(keeper, 'arena', 'dashboard');
  expect(headerLabels()).toEqual([
    'Übersicht',
    'Abspielen',
    'Statistik',
    'Rangliste',
    'Halle der Aufzeichnungen',
  ]);
  expect(bottomLabels('Mobile Navigation')).toEqual([
    'Start',
    'Abspielen',
    'Statistik',
    'Wettkampf',
    'Wiederholungen',
  ]);

  shell(keeper, 'workshop', 'training');
  expect(headerLabels()).toEqual(['Training', 'KI-Tests', 'Modelle', 'Worker']);
});
