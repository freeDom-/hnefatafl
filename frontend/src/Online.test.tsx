import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Online from './Online';
import fixture from './onlineFixture.testdata.json';
import type { OnlineGame } from './onlineTypes';
import { setLanguage } from './i18n';
const identity = vi.hoisted(() => ({ id: '', permissions: ['play'] }));
vi.mock('./Auth', () => ({
  useAuth: () => ({
    account: { id: identity.id, permissions: identity.permissions },
    logout: vi.fn(),
  }),
}));
let current: OnlineGame;
let simultaneousReturn: OnlineGame;
let eligible = true;
let enabled = true;
let unavailable = false;
let mutation: (path: string, body: Record<string, unknown>) => Promise<Response>;
const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);
const fetcher = vi.fn((path: string, options?: RequestInit): Promise<Response> => {
  if (options?.method === 'POST') return mutation(path, JSON.parse(String(options.body)));
  if (path === '/api/rulesets')
    return respond([
      { id: 'brandubh', name: 'Brandubh', size: 7 },
      { id: 'tablut', name: 'Tablut', size: 9 },
      { id: 'copenhagen', name: 'Copenhagen', size: 11 },
    ]);
  if (unavailable) return respond({ detail: 'Online state unavailable.' }, 503);
  if (path === '/api/online/availability') return respond({ eligible, enabled });
  if (path.includes('?limit=')) return respond({ games: [current], limit: 25, offset: 0 });
  if (path.endsWith('/replay')) return respond(fixture.replay);
  if (path.endsWith(fixture.simultaneous_return.id)) return respond(simultaneousReturn);
  if (path.endsWith(fixture.sequential_return.id)) return respond(fixture.sequential_return);
  return respond(current);
});
function room(game: OnlineGame = fixture.active as OnlineGame) {
  current = structuredClone(game);
  history.replaceState(null, '', `/online/games/${game.id}`);
  return render(<Online />);
}
function lobby() {
  history.replaceState(null, '', '/online');
  return render(<Online />);
}
async function move() {
  await screen.findByText('Attackers to move');
  // A visible position can precede its passive version-change effects.
  await act(async () => {});
  const { source, target } = fixture.moved.moves[0];
  fireEvent.click(screen.getAllByRole('gridcell')[source]);
  expect(screen.getAllByRole('gridcell')[source]).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(screen.getAllByRole('gridcell')[target]);
}
beforeEach(() => {
  simultaneousReturn = structuredClone(fixture.simultaneous_return) as OnlineGame;
  identity.id = fixture.account_id;
  identity.permissions = ['play'];
  eligible = true;
  enabled = true;
  unavailable = false;
  current = structuredClone(fixture.waiting) as OnlineGame;
  mutation = async () =>
    respond({ game: current, transition: 'created', action_accepted: true }, 201);
  sessionStorage.clear();
  fetcher.mockClear();
  vi.stubGlobal('fetch', fetcher);
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
  sessionStorage.clear();
  setLanguage('en', false);
});

it.each(
  ['attackers', 'defenders', 'random'].flatMap((side) =>
    ['single', 'simultaneous', 'sequential'].map((mode) => [side, mode]),
  ),
)('creates with side %s and mode %s using only the contract payload', async (side, mode) => {
  lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Creator side'), { target: { value: side } });
  fireEvent.change(screen.getByLabelText('Match mode'), { target: { value: mode } });
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  fireEvent.change(screen.getByLabelText('Seconds per move'), { target: { value: '3600' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create game' }));
  await screen.findByRole('heading', { name: 'Online game' });
  const post = fetcher.mock.calls.find(([, opts]) => opts?.method === 'POST')!;
  expect(JSON.parse(String(post[1]?.body))).toEqual({
    request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    ruleset: 'tablut',
    side_selection: side,
    mode,
    time_per_move: 3600,
  });
});
it('retains a creation UUID through uncertain outcomes and reload; recovery never automatically retries', async () => {
  mutation = async () => {
    throw new TypeError('network');
  };
  const view = lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Create game' }));
  await screen.findByText(/The outcome is uncertain/);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Retry retained request' })).toBeEnabled(),
  );
  const saved = sessionStorage.getItem('hnefatafl-online-intent');
  expect(saved).toContain('request_id');
  view.unmount();
  lobby();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Retry retained request' })).toBeEnabled(),
  );
  mutation = async () => respond({ detail: 'Creation request already exists.' }, 409);
  fireEvent.click(screen.getByRole('button', { name: 'My games' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => path.includes('mine=true'))).toBe(true),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry retained request' }));
  await screen.findByText(/State changed or adjudication/);
  const posts = fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST');
  expect(posts).toHaveLength(2);
  expect(posts[0][1]?.body).toBe(posts[1][1]?.body);
  expect(sessionStorage.getItem('hnefatafl-online-intent')).toBe(saved);
});
it.each([429, 422, 503, 403])(
  'handles creation denial %s without regenerating the intent or retrying',
  async (status) => {
    mutation = async () => respond({ detail: 'Sanitized server explanation.' }, status);
    lobby();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Create game' }));
    await screen.findByText('Sanitized server explanation.');
    expect(sessionStorage.getItem('hnefatafl-online-intent')).toContain('request_id');
    expect(fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
  },
);
it('keeps admitted-seat play after eligibility removal and confirms coordinates/version without optimistic movement', async () => {
  eligible = false;
  let finish!: (v: Response) => void;
  mutation = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  room();
  await move();
  expect(screen.getByText(/Creating and joining require/)).toBeVisible();
  const { source, target } = fixture.moved.moves[0];
  expect(screen.getAllByRole('gridcell')[source]).toHaveAccessibleName(/attacker/);
  expect(screen.getAllByRole('gridcell')[target]).toHaveAccessibleName(/empty/);
  fireEvent.click(screen.getAllByRole('gridcell')[target]);
  const posts = fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ version: 1, source, target });
  current = fixture.moved as OnlineGame;
  await act(async () => {
    finish(await respond({ game: current, transition: 'move', action_accepted: true }));
  });
  await screen.findByText('Action confirmed by the server.');
  expect(screen.getAllByRole('gridcell')[target]).toHaveAccessibleName(/attacker/);
});
it('uses the lifecycle result when a successful request finalizes timeout without accepting the action', async () => {
  mutation = async () => {
    current = fixture.timeout_response.game as OnlineGame;
    return respond(fixture.timeout_response);
  };
  room(fixture.timeout_active as OnlineGame);
  await move();
  await screen.findByText(/Your requested action was not accepted/);
  expect(screen.getByText('Defenders win')).toBeVisible();
  expect(current.position.winner).toBeNull();
  expect(screen.queryByRole('button', { name: 'Concede' })).toBeNull();
});
it('reloads after a stale version without replaying the mutation', async () => {
  mutation = async () => {
    current = fixture.moved as OnlineGame;
    return respond({ detail: 'Stale game version.' }, 409);
  };
  room();
  await move();
  await screen.findByText(/State changed or adjudication/);
  await screen.findByText('Defenders to move');
  expect(fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
});
it('retains the last confirmed board as stale on unavailable recovery', async () => {
  mutation = async () => {
    unavailable = true;
    return respond({ detail: 'Online state unavailable.' }, 503);
  };
  room();
  await move();
  await screen.findByText('Stale state. Reconnecting…');
  expect(screen.getByRole('grid')).toBeVisible();
  expect(
    screen.getAllByRole('gridcell').every((cell) => cell.getAttribute('aria-disabled') === 'true'),
  ).toBe(true);
});
it('dispatches the existing expired-session flow for 401 responses', async () => {
  const expired = vi.fn();
  window.addEventListener('authentication-required', expired);
  mutation = async () => respond({ detail: 'Authentication required' }, 401);
  room();
  await move();
  await waitFor(() => expect(expired).toHaveBeenCalledTimes(1));
  window.removeEventListener('authentication-required', expired);
});
it('shows unassigned random waiting seats, admission denial and creator cancellation', async () => {
  current = {
    ...fixture.waiting,
    creator_side: null,
    participants: { attackers: null, defenders: null },
  } as OnlineGame;
  enabled = false;
  room(current);
  await screen.findByText(/Random sides are assigned/);
  expect(screen.getAllByText(/Unassigned/)).toHaveLength(2);
  mutation = async () => {
    current = { ...current, status: 'cancelled', version: 1 };
    return respond({ game: current, transition: 'cancel', action_accepted: true });
  };
  fireEvent.click(screen.getByRole('button', { name: 'Cancel waiting game' }));
  await screen.findByText('Action confirmed by the server.');
  expect(screen.queryByRole('button', { name: 'Cancel waiting game' })).toBeNull();
});
it('joins only with eligible admission and shows read-only spectator controls', async () => {
  identity.id = 'other';
  room(fixture.waiting as OnlineGame);
  await screen.findByRole('button', { name: 'Join game' });
  mutation = async () => {
    current = fixture.active as OnlineGame;
    return respond({ game: current, transition: 'join', action_accepted: true });
  };
  fireEvent.click(screen.getByRole('button', { name: 'Join game' }));
  await screen.findByText('Attackers to move');
  expect(screen.queryByRole('button', { name: 'Concede' })).toBeNull();
  expect(screen.getAllByRole('gridcell')[0]).toHaveAttribute('aria-disabled', 'true');
});
it('replay navigation stays local and prevents historical moves, with no assistance calls', async () => {
  room(fixture.moved as OnlineGame);
  await screen.findByText('Defenders to move');
  fireEvent.click(screen.getByRole('button', { name: 'View replay' }));
  await screen.findByText('Historical replay · read only');
  const { source, target } = fixture.moved.moves[0];
  expect(screen.getAllByRole('gridcell')[source]).toHaveAccessibleName(/attacker/);
  fireEvent.click(screen.getAllByRole('gridcell')[source]);
  fireEvent.click(screen.getAllByRole('gridcell')[target]);
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getAllByRole('gridcell')[target]).toHaveAccessibleName(/attacker/);
  fireEvent.click(screen.getByRole('button', { name: 'Return to live board' }));
  expect(
    fetcher.mock.calls.every(
      ([path, opts]) => !path.includes('/api/games/') && opts?.method !== 'POST',
    ),
  ).toBe(true);
});
it.each(['simultaneous', 'sequential'] as const)(
  'discovers %s siblings and reads each independent game',
  async (mode) => {
    const game = fixture[mode] as OnlineGame;
    room(game);
    await screen.findByRole('button', { name: /Game 2 · Active · Version 1/ });
    const sibling = fixture[`${mode}_return`];
    expect(fetcher.mock.calls.some(([path]) => path.endsWith(sibling.id))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Game 2 · Active · Version 1/ }));
    await waitFor(() => expect(location.pathname).toBe(`/online/games/${sibling.id}`));
  },
);
it('shows awaiting adjudication using the device clock without inventing a terminal result', async () => {
  const game = { ...fixture.active, deadline: '2000-01-01T00:00:00Z' } as OnlineGame;
  room(game);
  await screen.findByText(/Awaiting adjudication/);
  expect(screen.getByRole('button', { name: 'Concede' })).toBeVisible();
  expect(fetcher.mock.calls.some(([, o]) => o?.method === 'POST')).toBe(false);
});
it('discards another account’s retained request and aborts reads during account switching', async () => {
  sessionStorage.setItem(
    'hnefatafl-online-intent',
    JSON.stringify({
      account_id: 'different',
      payload: {
        request_id: crypto.randomUUID(),
        ruleset: 'brandubh',
        side_selection: 'random',
        mode: 'single',
        time_per_move: 60,
      },
    }),
  );
  const view = lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  const signals = fetcher.mock.calls.map(([, o]) => o?.signal).filter(Boolean) as AbortSignal[];
  identity.id = 'next-account';
  view.rerender(<Online />);
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(screen.queryByRole('button', { name: 'Retry retained request' })).toBeNull();
});
it('translates online controls and handles keyboard selection in German', async () => {
  setLanguage('de', false);
  room();
  await screen.findByRole('heading', { name: 'Online-Partie' });
  const source = screen.getAllByRole('gridcell')[fixture.moved.moves[0].source];
  source.focus();
  fireEvent.keyDown(source, { key: 'ArrowRight' });
  expect(document.activeElement).not.toBe(source);
  expect(screen.getByRole('button', { name: 'Wiedergabe ansehen' })).toBeVisible();
});

it('refreshes a sibling result even when the selected game version stays unchanged', async () => {
  room(fixture.simultaneous as OnlineGame);
  await screen.findByRole('button', { name: /Game 2 · Active · Version 1/ });
  simultaneousReturn = fixture.simultaneous_return_completed as OnlineGame;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await screen.findByRole('button', { name: /Game 2 · Completed · Version 2/ });
  expect(current.version).toBe(1);
});

it('retains the submitted creation ID when two submit events arrive before rendering pending state', async () => {
  let reject!: (error: Error) => void;
  mutation = () =>
    new Promise((_resolve, failure) => {
      reject = failure;
    });
  lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  const form = screen.getByRole('button', { name: 'Create game' }).closest('form')!;
  act(() => {
    fireEvent.submit(form);
    fireEvent.submit(form);
  });
  const posts = fetcher.mock.calls.filter(([, options]) => options?.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(sessionStorage.getItem('hnefatafl-online-intent')!).payload.request_id).toBe(
    JSON.parse(String(posts[0][1]?.body)).request_id,
  );
  await act(async () => {
    reject(new TypeError('Connection lost after submit'));
  });
  await screen.findByText(/The outcome is uncertain/);
});
