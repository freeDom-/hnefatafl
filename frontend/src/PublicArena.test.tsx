import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PublicArena from './PublicArena';
import { PublicOnlineError, publicGame, publicGames } from './publicOnlineApi';
import { publicBoardView, type PublicGame, type PublicReplay } from './publicOnlineTypes';
import { setLanguage } from './i18n';

const position = {
  ruleset: 'brandubh',
  board: Array.from({ length: 49 }, (_, i) => (i === 24 ? 'K' : '.')),
  turn: 'attackers' as const,
  ply: 0,
  winner: null,
  reason: null,
};
const game: PublicGame = {
  id: 'public-game',
  category: 'online',
  ruleset: 'brandubh',
  ruleset_version: '1',
  status: 'active',
  mode: 'single',
  leg: 1,
  rated: true,
  created_at: '2026-11-15T00:00:00Z',
  participants: {
    attackers: {
      display_name: 'Archer',
      username: 'Archer',
      portrait_id: 'shieldbearer',
      start_rating: 1500,
      current_rating: 1510,
    },
    defenders: null,
  },
  result: null,
  position,
  deadline: null,
  time_per_move: 60,
  time_control: 'per_move',
  increment_seconds: 0,
  terminal_king_square: null,
  moves: null,
};
let fetchMock: ReturnType<typeof vi.fn>;
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status }));
}
beforeEach(() => {
  history.replaceState(null, '', '/watch');
  setLanguage('en', false);
  fetchMock = vi.fn(() => response({ items: [], next_cursor: null, limit: 12 }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('renders independently of Auth and uses only credential-free public GET requests', async () => {
  const expired = vi.fn();
  window.addEventListener('authentication-required', expired);
  render(<PublicArena />);
  await screen.findByText('The tables are quiet.');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toMatch(/^\/api\/public\/online\/games\?/);
  expect(options).toMatchObject({ method: 'GET', credentials: 'omit', cache: 'no-store' });
  expect(options.headers).toEqual({ Accept: 'application/json' });
  expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login');
  expect(screen.getByRole('link', { name: 'Create account' })).toHaveAttribute('href', '/register');
  expect(expired).not.toHaveBeenCalled();
  window.removeEventListener('authentication-required', expired);
});
it('bounds filters, resets paging on a filter change, and follows opaque cursors', async () => {
  fetchMock.mockImplementation((url: string) =>
    response({
      items: [game],
      next_cursor: url.includes('cursor=') ? null : 'opaque-next',
      limit: 12,
    }),
  );
  render(<PublicArena />);
  await screen.findByText('Archer');
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('cursor=opaque-next'));
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  fireEvent.change(screen.getByLabelText('Public player'), { target: { value: 'Müller' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
  await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('ruleset=tablut'));
  expect(fetchMock.mock.calls.at(-1)?.[0]).not.toContain('cursor=');
  expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('player=M%C3%BCller');
});
it('treats public-disabled 404 and rate limits as public errors without expiring accounts', async () => {
  fetchMock.mockImplementation(() => response({}, 404));
  render(<PublicArena />);
  await screen.findByText('Public viewing is unavailable.');
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('The tables are quiet.');
  fetchMock.mockImplementation(() => response({}, 429));
  await expect(publicGame('valid', new AbortController().signal)).rejects.toMatchObject({
    status: 429,
  });
});
it('builds a read-only BoardView without legal moves, hints, account fields or terminal overlay at earlier cursors', () => {
  const replay = {
    game: { ...game, terminal_king_square: 24 },
    validated: true,
    states: [position, { ...position, ply: 1 }],
    moves: [{ source: 1, target: 2 }],
  } satisfies PublicReplay;
  const view = publicBoardView(replay.game, replay, 0);
  expect(view.size).toBe(7);
  expect(view).not.toHaveProperty('legal_moves');
  expect(view).not.toHaveProperty('analysis');
  expect(view.capturedKingSquare).toBeNull();
  expect(publicBoardView(replay.game, replay, 99).capturedKingSquare).toBe(24);
});
it('shows live boards without mutation/advisor controls and rejects untrusted portrait paths', async () => {
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementation(() =>
    response({
      ...game,
      participants: {
        ...game.participants,
        attackers: { ...game.participants.attackers, portrait_id: '../../secret' },
      },
    }),
  );
  render(<PublicArena />);
  await screen.findByRole('grid');
  expect(screen.getAllByRole('gridcell')).toHaveLength(49);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('gridcell')[0]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(document.querySelector('.legal, .hint-source, .hint-target')).toBeNull();
});
it('navigates completed replay positions by buttons and keyboard without fetching analysis', async () => {
  history.replaceState(null, '', '/watch/games/public-game/replay');
  const replay = {
    game: { ...game, status: 'completed' },
    validated: true,
    states: [position, { ...position, ply: 1 }],
    moves: [{ source: 1, target: 2 }],
    analysis: { hint: { move: { source: 24, target: 0 } } },
  };
  fetchMock.mockImplementation(() => response(replay));
  render(<PublicArena />);
  await screen.findByText('Move 0 of 1');
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  fireEvent.keyDown(screen.getByRole('group', { name: 'Replay controls' }), { key: 'Home' });
  expect(screen.getByText('Move 0 of 1')).toBeVisible();
  expect(document.querySelector('.hint-source, .hint-target')).toBeNull();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it('pauses hidden live polling, resumes once, and aborts work on unmount', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementation(() => response(game));
  const rendered = render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, 'hidden', { configurable: true, value: true });
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  await act(async () => {
    fireEvent(document, new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  const signal = fetchMock.mock.calls.at(-1)![1].signal as AbortSignal;
  rendered.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
it('keeps waiting polling slow', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementation(() => response({ ...game, status: 'waiting' }));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(14999);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
it('bounds anonymous query input and rejects malformed ids before fetching', async () => {
  await publicGames(
    { ruleset: '', status: '', outcome: '', player: 'x'.repeat(300) },
    null,
    new AbortController().signal,
  );
  expect(
    new URL(fetchMock.mock.calls[0][0], 'http://localhost').searchParams.get('player'),
  ).toHaveLength(148);
  expect(() => publicGame('../private', new AbortController().signal)).toThrow(PublicOnlineError);
});

it('stops polling completed replays', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game/replay');
  fetchMock.mockImplementation(() =>
    response({
      game: { ...game, status: 'completed' },
      validated: true,
      states: [position],
      moves: [],
    }),
  );
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it('rejects delayed responses from a superseded filter request', async () => {
  let resolveOld!: (value: Response) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        resolveOld = resolve;
      }),
  );
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  render(<PublicArena />);
  const oldSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'copenhagen' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
  await screen.findByText('The tables are quiet.');
  expect(oldSignal.aborted).toBe(true);
  await act(async () => {
    resolveOld(new Response(JSON.stringify({ items: [game], next_cursor: null, limit: 12 })));
  });
  expect(screen.queryByText('Archer')).not.toBeInTheDocument();
});
