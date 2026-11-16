import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PublicArena, { matchPublicRoute } from './PublicArena';
import {
  PublicOnlineError,
  parseRetryAfter,
  publicGame,
  publicGames,
  publicReplay,
} from './publicOnlineApi';
import { portraitAssetSrc } from './portraitAssets';
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

it('keeps the final position and stops polling when a watched live game completes', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game');
  const completed: PublicGame = {
    ...game,
    status: 'completed',
    result: {
      winner: 'attackers',
      termination_reason: 'king_captured',
      rated: true,
      winner_delta: 12,
    },
  };
  const record: PublicReplay = {
    game: completed,
    validated: true,
    states: [
      position,
      { ...position, ply: 1, turn: 'defenders' },
      { ...position, ply: 2, winner: 'attackers', reason: 'king_captured' },
    ],
    moves: [
      { source: 3, target: 2 },
      { source: 24, target: 0 },
    ],
  };
  let detail = 0;
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith('/replay')) return response(record);
    detail += 1;
    return response(detail === 1 ? game : completed);
  });
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.queryByText('Move 0 of 2')).not.toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByText('Move 2 of 2')).toBeVisible();
  expect(screen.queryByText('Move 0 of 2')).not.toBeInTheDocument();
  expect(screen.getByText('Attackers won')).toBeVisible();
  const calls = fetchMock.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(fetchMock.mock.calls.length).toBe(calls);
});

it('times out an indefinitely pending public request and recovers on retry', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch');
  fetchMock.mockImplementation(
    (_url: string, options: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        );
      }),
  );
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(screen.getByText('Public viewing timed out. Please retry.')).toBeVisible();
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('The tables are quiet.')).toBeVisible();
});

it('preserves caller cancellation instead of reporting a timeout', async () => {
  fetchMock.mockImplementation(
    (_url: string, options: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        );
      }),
  );
  const controller = new AbortController();
  const pending = publicGames(
    { ruleset: '', status: '', outcome: '', player: '' },
    null,
    controller.signal,
  );
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
});

it('stops automatic polling when a replay cannot be validated', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game/replay');
  fetchMock.mockImplementation(() => response({}, 409));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('This replay could not be validated.')).toBeVisible();
  const calls = fetchMock.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(fetchMock.mock.calls.length).toBe(calls);
});

it('stops automatic discovery polling after a public-disabled 404', async () => {
  vi.useFakeTimers();
  fetchMock.mockImplementation(() => response({}, 404));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('Public viewing is unavailable.')).toBeVisible();
  const calls = fetchMock.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(fetchMock.mock.calls.length).toBe(calls);
});

it('parses Retry-After seconds and HTTP-date values with a cap', () => {
  expect(parseRetryAfter('2', 0)).toBe(2000);
  expect(parseRetryAfter('0', 0)).toBe(0);
  expect(parseRetryAfter(new Date(5000).toUTCString(), 0)).toBe(5000);
  expect(parseRetryAfter('not-a-date', 0)).toBe(0);
  expect(parseRetryAfter(null, 0)).toBe(0);
  expect(parseRetryAfter('999999', 0)).toBe(300000);
});

it('waits for a rate-limit Retry-After before the next discovery request', async () => {
  vi.useFakeTimers();
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(JSON.stringify({}), { status: 429, headers: { 'Retry-After': '30' } }),
    ),
  );
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('Public viewing is busy. Please retry shortly.')).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(29999);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('backs off before retrying a discovery server failure', async () => {
  vi.useFakeTimers();
  fetchMock.mockImplementationOnce(() => response({}, 500));
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(14999);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('classifies documented public routes and rejects malformed ids', () => {
  expect(matchPublicRoute('/watch')).toEqual({ kind: 'discovery' });
  expect(matchPublicRoute('/watch/')).toEqual({ kind: 'discovery' });
  expect(matchPublicRoute('/watch/games/abc-123')).toEqual({
    kind: 'game',
    id: 'abc-123',
    replay: false,
  });
  expect(matchPublicRoute('/watch/games/abc-123/replay')).toEqual({
    kind: 'game',
    id: 'abc-123',
    replay: true,
  });
  expect(matchPublicRoute('/watch/games/abc-123/replay/')).toEqual({
    kind: 'game',
    id: 'abc-123',
    replay: true,
  });
  expect(matchPublicRoute('/watch/games/')).toEqual({ kind: 'not-found' });
  expect(matchPublicRoute('/watch/games/bad!id')).toEqual({ kind: 'not-found' });
  expect(matchPublicRoute('/watch/games/abc-123/extra')).toEqual({ kind: 'not-found' });
  expect(matchPublicRoute('/watch/unknown')).toEqual({ kind: 'not-found' });
});

it('renders an accessible not-found state for unsupported public routes without fetching', () => {
  history.replaceState(null, '', '/watch/games/bad!id');
  render(<PublicArena />);
  expect(screen.getByRole('heading', { name: 'Public page not found.' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Back to public games' })).toHaveAttribute(
    'href',
    '/watch',
  );
  expect(fetchMock).not.toHaveBeenCalled();
});

it('shows a replacement portrait after the previous image failed to load', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game');
  let portrait = 'shieldbearer';
  fetchMock.mockImplementation(() =>
    response({
      ...game,
      participants: {
        ...game.participants,
        attackers: { ...game.participants.attackers!, portrait_id: portrait },
      },
    }),
  );
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const first = document.querySelector('img') as HTMLImageElement;
  fireEvent.error(first);
  expect(first.hidden).toBe(true);
  portrait = 'shieldmaiden';
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  const second = document.querySelector('img') as HTMLImageElement;
  expect(second.hidden).toBe(false);
  expect(second.getAttribute('src')).toContain('shieldmaiden.webp');
});

it('applies a rate-limit cooldown to manual retry and recovers after it expires', async () => {
  vi.useFakeTimers();
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(JSON.stringify({}), { status: 429, headers: { 'Retry-After': '30' } }),
    ),
  );
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('Public viewing is busy. Please retry shortly.')).toBeVisible();
  const cooldownNotice = 'Retry is temporarily unavailable. The viewer will retry automatically.';
  expect(screen.getByText(cooldownNotice)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Retry' })).toHaveAttribute('aria-disabled', 'true');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(screen.getByText('The tables are quiet.')).toBeVisible();
  expect(screen.queryByText(cooldownNotice)).not.toBeInTheDocument();
});

it('preserves the rate-limit cooldown across hidden and visible transitions', async () => {
  vi.useFakeTimers();
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(JSON.stringify({}), { status: 429, headers: { 'Retry-After': '30' } }),
    ),
  );
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, 'hidden', { configurable: true, value: true });
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(19999);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('applies a rate-limit cooldown to manual retry in the game viewer', async () => {
  vi.useFakeTimers();
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(JSON.stringify({}), { status: 429, headers: { 'Retry-After': '20' } }),
    ),
  );
  fetchMock.mockImplementation(() => response(game));
  render(<PublicArena />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('Public viewing is busy. Please retry shortly.')).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('grid')).toBeVisible();
});

it('uses server-authoritative caching for detail and replay but keeps discovery uncached', async () => {
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  await publicGames(
    { ruleset: '', status: '', outcome: '', player: '' },
    null,
    new AbortController().signal,
  );
  expect(fetchMock.mock.calls.at(-1)?.[1]).toMatchObject({ cache: 'no-store' });

  fetchMock.mockImplementation(() => response(game));
  await publicGame('valid', new AbortController().signal);
  expect(fetchMock.mock.calls.at(-1)?.[1]).toMatchObject({ cache: 'default' });

  fetchMock.mockImplementation(() =>
    response({ game, validated: true, states: [position], moves: [] }),
  );
  await publicReplay('valid', new AbortController().signal);
  expect(fetchMock.mock.calls.at(-1)?.[1]).toMatchObject({ cache: 'default' });
});

it('translates public spectator controls and replay labels into German', async () => {
  setLanguage('de', false);
  fetchMock.mockImplementation(() => response({ items: [], next_cursor: null, limit: 12 }));
  const { unmount } = render(<PublicArena />);
  await screen.findByText('An den Tischen ist es still.');
  expect(screen.getByRole('option', { name: 'Angreifer gewinnen' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Verteidiger gewinnen' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Zum Inhalt springen' })).toBeInTheDocument();
  unmount();
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
  expect(await screen.findByText('WIEDERGABE')).toBeVisible();
  expect(screen.getByRole('group', { name: 'Wiedergabesteuerung' })).toBeInTheDocument();
});

it('shows the active side to move for a live game', async () => {
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementation(() =>
    response({ ...game, position: { ...position, turn: 'defenders' } }),
  );
  render(<PublicArena />);
  await screen.findByRole('grid');
  expect(screen.getByText('Defenders to move')).toBeVisible();
});

it('formats the time control with the shared duration formatter', async () => {
  history.replaceState(null, '', '/watch/games/public-game');
  fetchMock.mockImplementation(() =>
    response({ ...game, time_control: 'total', time_per_move: 3600, increment_seconds: 30 }),
  );
  render(<PublicArena />);
  await screen.findByRole('grid');
  expect(screen.getByText(/1h 00m 00s/)).toBeVisible();
  expect(screen.getByText(/\+ 30s/)).toBeVisible();
});

it('exposes bundled portrait URLs only for allowlisted ids', () => {
  expect(portraitAssetSrc('shieldbearer')).toBe(
    '/assets/arena-v2/assets/portraits/shieldbearer.webp',
  );
  expect(portraitAssetSrc('../../secret')).toBeNull();
  expect(portraitAssetSrc('unknown-portrait')).toBeNull();
});
