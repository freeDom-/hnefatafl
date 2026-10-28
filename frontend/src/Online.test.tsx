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
let switcherGames: OnlineGame[] = [];
let eligible = true;
let enabled = true;
let unavailable = false;
let mutation: (path: string, body: Record<string, unknown>) => Promise<Response>;
let replayResponse: typeof fixture.replay = fixture.replay;
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
  if (path.includes('/online/games?'))
    return respond({ games: [current, ...switcherGames], limit: 25, offset: 0, section: 'all' });
  if (path.endsWith('/replay')) return respond(replayResponse);
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
  await screen.findByLabelText('Attackers to move');
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
  replayResponse = structuredClone(fixture.replay);
  current = structuredClone(fixture.waiting) as OnlineGame;
  switcherGames = [];
  mutation = async () =>
    respond({ game: current, transition: 'created', action_accepted: true }, 201);
  sessionStorage.clear();
  fetcher.mockClear();
  vi.stubGlobal('fetch', fetcher);
  setLanguage('en', false);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
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
  expect(
    screen.queryByText('Disconnecting, closing the browser or logging out does not pause clocks.'),
  ).toBeNull();
  fireEvent.change(screen.getByLabelText('Creator side'), { target: { value: side } });
  fireEvent.change(screen.getByLabelText('Match mode'), { target: { value: mode } });
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  fireEvent.change(screen.getByLabelText('Days'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Hours'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Minutes'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create game' }));
  await screen.findByRole('heading', { name: 'Online game' });
  const post = fetcher.mock.calls.find(([, opts]) => opts?.method === 'POST')!;
  expect(JSON.parse(String(post[1]?.body))).toEqual({
    request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    ruleset: 'tablut',
    side_selection: side,
    mode,
    time_per_move: 3600,
    rated: true,
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
  fireEvent.click(screen.getByRole('tab', { name: 'My games' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => path.includes('section=mine'))).toBe(true),
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
it('shows authoritative rating changes on a completed rated game', async () => {
  const rated = structuredClone(fixture.rated_completed) as OnlineGame;
  room(rated);
  await screen.findByRole('heading', { name: 'Rating changes' });
  expect(screen.getByText(/\+16\.0 → 1516/)).toBeVisible();
  expect(screen.getByText(/-16\.0 → 1484/)).toBeVisible();
  expect(screen.getAllByText(/Provisional/).length).toBeGreaterThan(0);
});
it('does not show rating changes when the result carries no rating applications', async () => {
  const unrated = {
    ...fixture.completed,
    rated: false,
    result: { ...fixture.completed.result!, rating: null, rated_eligible: false },
  } as OnlineGame;
  room(unrated);
  await screen.findByText('Attackers win');
  expect(screen.queryByRole('heading', { name: 'Rating changes' })).toBeNull();
});
it('reloads after a stale version without replaying the mutation', async () => {
  mutation = async () => {
    current = fixture.moved as OnlineGame;
    return respond({ detail: 'Stale game version.' }, 409);
  };
  room();
  await move();
  await screen.findByText(/State changed or adjudication/);
  await screen.findByLabelText('Defenders to move');
  expect(fetcher.mock.calls.filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
});
it('retains the last confirmed board as stale on unavailable recovery', async () => {
  mutation = async () => {
    unavailable = true;
    return respond({ detail: 'Online state unavailable.' }, 503);
  };
  room();
  await move();
  await screen.findByText('Online service unavailable or stale. Retry to reconnect.');
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
    match: { ...fixture.waiting.match, side_selection: 'random', first_creator_side: null },
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
  await screen.findByLabelText('Attackers to move');
  expect(screen.queryByRole('button', { name: 'Concede' })).toBeNull();
  expect(screen.getAllByRole('gridcell')[0]).toHaveAttribute('aria-disabled', 'true');
});
it('replay navigation stays local and prevents historical moves, with no assistance calls', async () => {
  room(fixture.moved as OnlineGame);
  await screen.findByLabelText('Defenders to move');
  fireEvent.click(screen.getByRole('button', { name: 'View replay' }));
  await screen.findByRole('button', { name: 'Return to live board' });
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
it('shows the payload captured counts and follows the replay cursor', async () => {
  room({ ...fixture.moved, captured: { attackers: 2, defenders: 3 } } as OnlineGame);
  await screen.findByLabelText('Defenders to move');
  const capturedRow = () => screen.getByText('Captured').closest('.captured-row')!;
  const counts = () => capturedRow().textContent?.replace(/\s+/g, ' ').trim();
  expect(counts()).toBe('Captured 2 3');
  const shortened = structuredClone(fixture.replay);
  const board = shortened.states[0].board;
  board[board.indexOf('A')] = '.';
  board[board.indexOf('D')] = '.';
  replayResponse = shortened;
  fireEvent.click(screen.getByRole('button', { name: 'View replay' }));
  await screen.findByRole('button', { name: 'Return to live board' });
  expect(counts()).toBe('Captured 3 4');
});
it.each(['simultaneous', 'sequential'] as const)(
  'discovers %s siblings in the active-game switcher and switches games',
  async (mode) => {
    const game = fixture[mode] as OnlineGame;
    const sibling = fixture[`${mode}_return`] as OnlineGame;
    // A completed first sequential leg leaves only one active game; add another
    // so the >1-active switcher rule is exercised.
    switcherGames =
      mode === 'sequential' ? [sibling, { ...sibling, id: 'online-' + 'c'.repeat(32) }] : [sibling];
    room(game);
    const button = await waitFor(() => {
      const found = document.querySelector(`[data-game-id="${sibling.id}"]`);
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    fireEvent.click(button);
    await waitFor(() => expect(location.pathname).toBe(`/online/games/${sibling.id}`));
    await screen.findByRole('heading', { name: 'Online game' });
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

it('marks the current game and games awaiting the user move in the switcher', async () => {
  const theirs = structuredClone(fixture.active) as OnlineGame;
  theirs.id = 'online-' + 'b'.repeat(32);
  theirs.position = { ...theirs.position, turn: 'defenders' };
  switcherGames = [theirs];
  room(fixture.active as OnlineGame);
  await waitFor(() => {
    expect(document.querySelector(`[data-game-id="${fixture.active.id}"]`)).not.toBeNull();
  });
  const currentButton = document.querySelector(
    `[data-game-id="${fixture.active.id}"]`,
  ) as HTMLElement;
  expect(currentButton).toHaveAttribute('aria-current', 'page');
  expect(currentButton.textContent).toContain('Your turn');
  const otherButton = document.querySelector(`[data-game-id="${theirs.id}"]`) as HTMLElement;
  expect(otherButton.textContent).not.toContain('Your turn');
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

it('opens personal review with scoped reads only, effective void status and local replay controls', async () => {
  current = {
    ...fixture.moved,
    status: 'voided',
    result: {
      ...fixture.timeout_response.game.result,
      winner: null,
      termination_reason: 'administrative_void',
      competitive_eligible: false,
    },
  } as OnlineGame;
  history.replaceState(null, '', `/online/games/${current.id}/review`);
  render(<Online />);
  await screen.findByRole('heading', { name: 'Online game review' });
  await screen.findByRole('button', { name: 'Next move' });
  expect(screen.getByText('Lifecycle: Voided')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Concede' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Return to live board' })).toBeNull();
  expect(
    fetcher.mock.calls.every(
      ([path, opts]) => path.startsWith('/api/personal/online/') && opts?.method !== 'POST',
    ),
  ).toBe(true);
});

it('switches lobby tabs and requests the matching section', async () => {
  lobby();
  await waitFor(() => expect(screen.getByRole('tab', { name: 'All games' })).toBeVisible());
  expect(screen.getByRole('tab', { name: 'All games' })).toHaveAttribute('aria-selected', 'true');
  expect(fetcher.mock.calls.some(([path]) => path.includes('section=all'))).toBe(true);
  fireEvent.click(screen.getByRole('tab', { name: 'My turn' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => path.includes('section=my_turn'))).toBe(true),
  );
  expect(screen.getByRole('tab', { name: 'My turn' })).toHaveAttribute('aria-selected', 'true');
});
it('shows the shared remaining time in the game details for an active game', async () => {
  const game = {
    ...fixture.active,
    deadline: new Date(Date.now() + 125_000).toISOString(),
  } as OnlineGame;
  room(game);
  await screen.findByText('Remaining time');
  expect(screen.getByLabelText(/2m 0[0-9]s remaining/)).toBeVisible();
  expect(screen.queryByText('Awaiting adjudication')).toBeNull();
});
it('replaces inline creation with the shared modal and closes it on Escape or Cancel', async () => {
  history.replaceState(null, '', '/dashboard');
  const { Lobby } = await import('./Online');
  const view = render(
    <Lobby accountId={identity.id} open={vi.fn()} embedded creationRequested={0} />,
  );
  await screen.findByRole('heading', { name: 'Online games' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull();
  expect(screen.queryByText('Connected · refreshes automatically')).toBeNull();
  view.rerender(<Lobby accountId={identity.id} open={vi.fn()} embedded creationRequested={1} />);
  const dialog = await screen.findByRole('dialog', { name: 'Create an online game' });
  expect(screen.getByLabelText('Ruleset')).toHaveFocus();
  fireEvent(dialog, new Event('cancel', { cancelable: true, bubbles: true }));
  expect(screen.queryByRole('dialog')).toBeNull();
  view.rerender(<Lobby accountId={identity.id} open={vi.fn()} embedded creationRequested={2} />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});
it('retries a retained 61-second creation intent exactly without truncation to a minute', async () => {
  const payload = {
    request_id: crypto.randomUUID(),
    ruleset: 'brandubh',
    side_selection: 'random',
    mode: 'single',
    time_per_move: 61,
  };
  sessionStorage.setItem(
    'hnefatafl-online-intent',
    JSON.stringify({ account_id: identity.id, payload }),
  );
  lobby();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Retry retained request' })).toBeEnabled(),
  );
  expect(screen.getByText(/uses exactly 1m 01s per move/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry retained request' }));
  await screen.findByRole('heading', { name: 'Online game' });
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, o]) => o?.method === 'POST')![1]?.body)),
  ).toEqual({ ...payload, rated: true });
});
it('creates every new online game as rated with no casual selector', async () => {
  lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  expect(screen.queryByLabelText('Game type')).toBeNull();
  expect(screen.queryByText(/Casual games do not affect ratings/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Create game' }));
  await screen.findByRole('heading', { name: 'Online game' });
  const post = fetcher.mock.calls.find(([, opts]) => opts?.method === 'POST')!;
  expect(JSON.parse(String(post[1]?.body))).toMatchObject({ rated: true });
});
it.each([
  ['0', '0', '0'],
  ['14', '0', '1'],
])('rejects the invalid combined duration %s/%s/%s before posting', async (d, h, m) => {
  lobby();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create game' })).toBeEnabled());
  for (const [label, value] of [
    ['Days', d],
    ['Hours', h],
    ['Minutes', m],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.submit(screen.getByRole('button', { name: 'Create game' }).closest('form')!);
  await screen.findByRole('alert');
  expect(fetcher.mock.calls.some(([, o]) => o?.method === 'POST')).toBe(false);
});
