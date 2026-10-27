import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Leaderboard from './Leaderboard';
import { setLanguage } from './i18n';

const identity = vi.hoisted(() => ({ id: 'archer' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: { id: identity.id } }) }));

const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);

const board = (ruleset = 'tablut') => ({
  ruleset,
  policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
  qualified: {
    entries: [
      {
        account_id: 'archer',
        name: 'Archer',
        rating: 1516,
        rated_games: 12,
        wins: 7,
        losses: 5,
        draws: 0,
        rank: 1,
      },
      {
        account_id: 'keeper',
        name: 'Keeper',
        rating: 1484,
        rated_games: 12,
        wins: 5,
        losses: 7,
        draws: 0,
        rank: 2,
      },
    ],
    total: 2,
    limit: 25,
    offset: 0,
  },
  provisional: {
    entries: [
      {
        account_id: 'visitor',
        name: 'Visitor',
        rating: 1500,
        rated_games: 3,
        wins: 1,
        losses: 1,
        draws: 1,
        rank: 1,
      },
    ],
  },
  me: {
    account_id: 'archer',
    name: 'Archer',
    rating: 1516,
    rated_games: 12,
    wins: 7,
    losses: 5,
    draws: 0,
    rank: 1,
    provisional: false,
  },
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

it('shows qualified, provisional and the signed-in player standing', async () => {
  render(<Leaderboard />);
  expect(screen.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  await screen.findByText(/Archer/);
  expect(screen.getByText('Keeper')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Qualified standings' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Provisional players' })).toBeVisible();
  expect(screen.getByText(/Your standing/)).toBeVisible();
  expect(screen.getByText(/Provisional until 10 rated games/)).toBeVisible();
  // The signed-in row is marked.
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
  serve = () =>
    respond({
      ...board(),
      qualified: { entries: [], total: 0, limit: 25, offset: 0 },
      provisional: { entries: [] },
      me: null,
    });
  render(<Leaderboard />);
  await screen.findByText('No rated games in this pool yet.');
});

it('shows an unranked state for an empty personal pool', async () => {
  serve = () =>
    respond({
      ...board(),
      provisional: { entries: [] },
      me: null,
    });
  render(<Leaderboard />);
  await screen.findByText('You are not ranked in this pool yet.');
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

it('paginates the qualified standings in the full view', async () => {
  const entries = Array.from({ length: 30 }, (_, index) => ({
    account_id: `account-${index}`,
    name: `Player ${index}`,
    rating: 2000 - index,
    rated_games: 20,
    wins: 10,
    losses: 10,
    draws: 0,
    rank: index + 1,
  }));
  serve = (url) => {
    const offset = Number(new URL(url, 'http://x').searchParams.get('offset') ?? '0');
    return respond({
      ...board(),
      qualified: { entries: entries.slice(offset, offset + 25), total: 30, limit: 25, offset },
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
  await screen.findByRole('heading', { name: 'Qualifizierte Platzierungen' });
  expect(screen.getByRole('heading', { name: 'Rangliste' })).toBeVisible();
});
