import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Leaderboard from './Leaderboard';
import { setLanguage } from './i18n';

const identity = vi.hoisted(() => ({ id: 'archer' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: { id: identity.id } }) }));

const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);

const entry = (
  account_id: string,
  name: string,
  rating: number,
  rated_games: number,
  rank: number,
  provisional = false,
) => ({ account_id, name, rating, rated_games, wins: 1, losses: 1, draws: 1, rank, provisional });

const board = (ruleset = 'tablut') => ({
  ruleset,
  policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
  entries: [entry('archer', 'Archer', 1516, 12, 1), entry('keeper', 'Keeper', 1500, 3, 2, true)],
  total: 2,
  limit: 25,
  offset: 0,
  me: entry('archer', 'Archer', 1516, 12, 1),
});

let serve: (url: string) => Promise<Response>;
const fetcher = vi.fn((url: string) => serve(url));
beforeEach(() => {
  identity.id = 'archer';
  setLanguage('en', false);
  fetcher.mockClear();
  serve = (url) => respond(board(url.includes('brandubh') ? 'brandubh' : 'tablut'));
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

it('shows one unified list with provisional players and the signed-in row', async () => {
  render(<Leaderboard />);
  expect(screen.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  await screen.findByText(/Archer/);
  expect(screen.getByText('Keeper')).toBeVisible();
  // One table, no separate provisional section or standing paragraph.
  expect(document.querySelectorAll('table.leaderboard')).toHaveLength(1);
  expect(screen.queryByText('Qualified standings')).toBeNull();
  expect(screen.queryByText('Provisional players')).toBeNull();
  expect(screen.queryByText(/Your standing/)).toBeNull();
  expect(screen.getByText('Provisional')).toBeVisible();
  expect(screen.getByText(/Archer/).closest('tr')).toHaveClass('you');
});

it('refetches when the ruleset changes', async () => {
  render(<Leaderboard />);
  await screen.findByText(/Archer/);
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'brandubh' } });
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([url]) => url.includes('ruleset=brandubh'))).toBe(true),
  );
});

it('shows an empty pool state', async () => {
  serve = () => respond({ ...board(), entries: [], total: 0, me: null });
  render(<Leaderboard />);
  await screen.findByText('No rated games in this pool yet.');
});

it('shows a stale error with retry', async () => {
  let fail = true;
  serve = () => (fail ? respond({ detail: 'down' }, 503) : respond(board()));
  render(<Leaderboard />);
  await screen.findByRole('button', { name: 'Retry' });
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText(/Archer/);
});

it('compact mode links to the full leaderboard without pagination', async () => {
  render(<Leaderboard compact />);
  await screen.findByText(/Archer/);
  expect(screen.getByRole('link', { name: 'View full leaderboard' })).toHaveAttribute(
    'href',
    '/leaderboard',
  );
  expect(screen.queryByRole('button', { name: 'Next page' })).toBeNull();
});

it('paginates the unified standings in the full view', async () => {
  const entries = Array.from({ length: 30 }, (_, index) =>
    entry(`account-${index}`, `Player ${index}`, 2000 - index, 20, index + 1),
  );
  serve = (url) => {
    const offset = Number(new URL(url, 'http://x').searchParams.get('offset') ?? '0');
    return respond({
      ...board(),
      entries: entries.slice(offset, offset + 25),
      total: 30,
      offset,
      me: null,
    });
  };
  render(<Leaderboard />);
  await screen.findByText('Player 0');
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([url]) => url.includes('offset=25'))).toBe(true),
  );
});

it('translates the leaderboard in German', async () => {
  setLanguage('de', false);
  render(<Leaderboard />);
  await screen.findByText(/Archer/);
  expect(screen.getByRole('heading', { name: 'Rangliste' })).toBeVisible();
  expect(screen.getByText('Vorläufig')).toBeVisible();
});
