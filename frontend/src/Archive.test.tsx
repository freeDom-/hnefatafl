import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Archive from './Archive';
import fixture from './onlineFixture.testdata.json';
import userEvent from '@testing-library/user-event';
import { I18nProvider, setLanguage } from './i18n';
const identity = vi.hoisted(() => ({ id: 'archer', permissions: ['play'] }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
const row = {
  id: fixture.replay.game.id,
  category: 'online',
  ruleset: 'brandubh',
  status: 'completed',
  winner: 'attackers',
  attackers: 'Archer',
  defenders: 'Keeper',
  recorded_at: '2035-01-01T00:00:00Z',
};
const page = {
  games: [row],
  totals: [
    {
      category: 'online',
      games: 21,
      completed: 21,
      attackers_wins: 21,
      defenders_wins: 0,
      draws: 0,
    },
  ],
  matching: 21,
  limit: 20,
  next_cursor: 'page-two',
};
const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status === 200, status, json: async () => body } as Response);
const fetcher = vi.fn<(url: string, opts?: RequestInit) => Promise<Response>>();
beforeEach(() => {
  setLanguage('en', false);
  identity.id = 'archer';
  identity.permissions = ['play'];
  history.replaceState(null, '', '/archive');
  fetcher.mockReset();
  fetcher.mockImplementation((url) => respond(url.includes('/replay') ? fixture.replay : page));
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});
it('uses shared dense rows, server filters/pages and has no Local players or account filters for normal users', async () => {
  render(<Archive />);
  const rowLink = await screen.findByRole('link', { name: 'Open review: Archer vs Keeper' });
  expect(rowLink.closest('table')).toHaveClass('recorded-games');
  expect(screen.queryByText('Local players')).toBeNull();
  expect(screen.queryByLabelText('Game type')).toBeNull();
  expect(screen.queryByLabelText('Player name')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([url]) => url.includes('cursor=page-two'))).toBe(true),
  );
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([url]) => url.includes('ruleset=tablut') && !url.includes('cursor='),
      ),
    ).toBe(true),
  );
  expect(fetcher.mock.calls.every(([url]) => url.startsWith('/api/history/'))).toBe(true);
});
it('keeps the exact selected review visible when it is outside the filtered page, including direct load', async () => {
  history.replaceState(null, '', `/archive?ruleset=tablut&review=online&selected=${row.id}`);
  fetcher.mockImplementation((url) =>
    respond(
      url.includes('/replay') ? fixture.replay : { ...page, games: [], matching: 0, totals: [] },
    ),
  );
  render(<Archive />);
  await screen.findByRole('grid');
  expect(screen.queryByLabelText('Ruleset')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  fireEvent.click(screen.getByRole('link', { name: 'Back to Hall of records' }));
  await screen.findByLabelText('Ruleset');
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'copenhagen' } });
  expect(location.search).toContain('selected=' + row.id);
  fireEvent.click(screen.getByRole('link', { name: 'Return to selected review' }));
  await screen.findByRole('grid');
  expect(fetcher.mock.calls.every(([, opts]) => opts?.method === 'GET')).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => url.includes('/api/games/'))).toBe(false);
});
it('exposes administrator category/name filters without fetching setup players', async () => {
  identity.permissions = ['play', 'all_data'];
  render(<Archive />);
  await screen.findByRole('link', { name: 'Open review: Archer vs Keeper' });
  fireEvent.change(screen.getByLabelText('Game type'), {
    target: { value: 'account-single-player' },
  });
  fireEvent.change(screen.getByLabelText('Player name'), { target: { value: 'Keeper' } });
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([url]) => url.includes('category=account-single-player') && url.includes('player=Keeper'),
      ),
    ).toBe(true),
  );
  expect(fetcher.mock.calls.every(([url]) => !url.includes('/players'))).toBe(true);
});
it('clears old history and aborts a delayed read on account changes', async () => {
  let finish!: (value: Response) => void;
  let signal: AbortSignal | null = null;
  fetcher.mockImplementation((_, opts) => {
    signal = opts?.signal as AbortSignal;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const view = render(<Archive />);
  const oldSignal = signal!;
  identity.id = 'keeper';
  fetcher.mockImplementation(() => respond({ ...page, games: [], matching: 0 }));
  view.rerender(<Archive />);
  await screen.findByText('No games match these filters.');
  await act(async () => finish(await respond(page)));
  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByRole('link', { name: 'Open review: Archer vs Keeper' })).toBeNull();
});

it.each(['Shared name', 'Archer', '@Archer', 'Shared name (@Archer)'])(
  'submits the literal administrator identity search %s and resets its cursor',
  async (query) => {
    identity.permissions = ['play', 'all_data'];
    history.replaceState(null, '', '/archive?ruleset=brandubh&cursor=old-page');
    render(<Archive />);
    const input = await screen.findByRole('textbox', { name: 'Player name' });
    fireEvent.change(input, { target: { value: query } });
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([url]) => {
          const params = new URL(url, 'http://test').searchParams;
          return (
            params.get('player') === query &&
            params.get('ruleset') === 'brandubh' &&
            !params.has('cursor')
          );
        }),
      ).toBe(true),
    );
    expect(input).toHaveValue(query);
  },
);

it.each(['en', 'de'] as const)(
  'accepts a full 148-code-point pasted identity in %s',
  async (language) => {
    identity.permissions = ['play', 'all_data'];
    render(
      <I18nProvider>
        <Archive />
      </I18nProvider>,
    );
    act(() => setLanguage(language, false));
    const input = await screen.findByRole('textbox', {
      name: language === 'de' ? 'Spielername' : 'Player name',
    });
    const label = '😀'.repeat(80) + ' (@' + 'W'.repeat(64) + ')';
    expect([...label]).toHaveLength(148);
    const user = userEvent.setup();
    await user.click(input);
    await user.paste(label);
    expect(input).toHaveValue(label);
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([url]) => new URL(url, 'http://test').searchParams.get('player') === label,
        ),
      ).toBe(true),
    );
    await user.keyboard('x');
    expect(input).toHaveValue(label);
    expect(new URLSearchParams(location.search).get('player')).toBe(label);
  },
);
