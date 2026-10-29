import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ActiveGameSwitcher from './ActiveGameSwitcher';
import { setLanguage } from './i18n';
import { DesktopPlayContext, MobilePlayContext } from './mobilePlay';

const singleGame = (id: string, overrides: Record<string, unknown> = {}) => ({
  id,
  ruleset: 'brandubh',
  winner: null,
  recorded_winner: null,
  ...overrides,
});
const onlineGame = (
  id: string,
  status: string,
  turn = 'attackers',
  overrides: Record<string, unknown> = {},
) => ({
  id,
  ruleset: 'tablut',
  status,
  match: { mode: 'single', leg: 1 },
  participants: {
    attackers: { account_id: 'me', name: 'Me' },
    defenders: { account_id: 'other', name: 'Other' },
  },
  position: { turn },
  ...overrides,
});

let singles: unknown[] = [];
let onlines: unknown[] = [];
const respond = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: async () => body } as Response);
const fetcher = vi.fn((path: string) => {
  if (path === '/api/games') return respond(singles);
  if (path.includes('/online/games?'))
    return respond({ games: onlines, limit: 50, offset: 0, section: 'mine' });
  return respond({});
});

beforeEach(() => {
  singles = [];
  onlines = [];
  vi.stubGlobal('fetch', fetcher);
  fetcher.mockClear();
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('renders nothing when no active games remain', async () => {
  singles = [
    singleGame('finished', { winner: 'attackers' }),
    singleGame('replay', { recorded_winner: 'defenders' }),
  ];
  onlines = [onlineGame('waiting', 'waiting'), onlineGame('completed', 'completed')];
  await act(async () => {
    render(<ActiveGameSwitcher accountId="me" currentId="finished" open={vi.fn()} />);
  });
  expect(screen.queryByRole('navigation', { name: 'Active games' })).toBeNull();
});

it.each(['single', 'online'] as const)(
  'renders nothing when the sole active %s game is already open',
  async (type) => {
    if (type === 'single') singles = [singleGame('active')];
    else onlines = [onlineGame('active', 'active')];
    await act(async () => {
      render(<ActiveGameSwitcher accountId="me" currentId="active" open={vi.fn()} />);
    });
    expect(screen.queryByRole('navigation', { name: 'Active games' })).toBeNull();
  },
);

it.each(['single', 'online'] as const)(
  'offers the sole active %s game from a finished game and hides after switching',
  async (type) => {
    singles = [singleGame('finished', { recorded_winner: 'attackers' })];
    if (type === 'single') singles.push(singleGame('active'));
    else onlines = [onlineGame('active', 'active')];
    const open = vi.fn();
    const view = render(<ActiveGameSwitcher accountId="me" currentId="finished" open={open} />);
    const nav = await screen.findByRole('navigation', { name: 'Active games' });
    const buttons = within(nav).getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAttribute('data-game-id', 'active');
    expect(buttons[0]).not.toHaveAttribute('aria-current');
    fireEvent.click(buttons[0]);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: 'active', type }));
    view.rerender(<ActiveGameSwitcher accountId="me" currentId="active" open={open} />);
    expect(screen.queryByRole('navigation', { name: 'Active games' })).toBeNull();
  },
);

it('lists active games across both types and marks current and your-turn games', async () => {
  singles = [singleGame('s1'), singleGame('s2', { winner: 'attackers' })];
  onlines = [onlineGame('o1', 'active', 'attackers'), onlineGame('o2', 'completed')];
  const open = vi.fn();
  render(<ActiveGameSwitcher accountId="me" currentId="o1" open={open} />);
  const nav = await screen.findByRole('navigation', { name: 'Active games' });
  const buttons = within(nav).getAllByRole('button');
  expect(buttons).toHaveLength(2);
  const current = nav.querySelector('[data-game-id="o1"]') as HTMLElement;
  expect(current).toHaveAttribute('aria-current', 'page');
  expect(current.textContent).toContain('Your turn');
  const single = nav.querySelector('[data-game-id="s1"]') as HTMLElement;
  expect(single.textContent).toContain('Single player game');
  fireEvent.click(single);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: 's1', type: 'single' }));
});

function renderList(open = vi.fn()) {
  return render(
    <MobilePlayContext.Provider value>
      <ActiveGameSwitcher accountId="me" currentId="s1" open={open} />
    </MobilePlayContext.Provider>,
  );
}

it.each(['mobile', 'desktop'])(
  'highlights own turns in the %s My games list independently of the open game',
  async (mode) => {
    const players = {
      attackers: { kind: 'human', account_id: 'me', name: 'Me' },
      defenders: { kind: 'ai', name: 'AI' },
    };
    singles = [
      singleGame('human-turn', { turn: 'attackers', players }),
      singleGame('ai-turn', { turn: 'defenders', players }),
    ];
    onlines = [
      onlineGame('your-turn', 'active'),
      onlineGame('opponent-turn', 'active', 'defenders'),
      onlineGame('waiting', 'waiting'),
    ];
    const Context = mode === 'mobile' ? MobilePlayContext : DesktopPlayContext;
    render(
      <Context.Provider value>
        <ActiveGameSwitcher accountId="me" currentId="opponent-turn" open={vi.fn()} />
      </Context.Provider>,
    );
    const nav = await screen.findByRole('navigation', { name: 'My games' });
    await waitFor(() => expect(within(nav).getAllByRole('button')).toHaveLength(5));
    for (const id of ['human-turn', 'your-turn']) {
      const button = nav.querySelector(`[data-game-id="${id}"]`)!;
      expect(button).toHaveClass('primary');
      expect(button).toHaveTextContent('Your turn');
    }
    for (const id of ['ai-turn', 'opponent-turn', 'waiting'])
      expect(nav.querySelector(`[data-game-id="${id}"]`)).toHaveClass('secondary');
    expect(nav.querySelector('[data-game-id="opponent-turn"]')).toHaveAttribute(
      'aria-current',
      'page',
    );
  },
);

it('shows loading, empty, and single-current-game states in My games', async () => {
  singles = [];
  onlines = [];
  const empty = renderList();
  expect(screen.getByRole('status')).toHaveTextContent('Loading running games');
  await screen.findByText('No running games.');
  empty.unmount();
  singles = [singleGame('s1')];
  renderList();
  const nav = await screen.findByRole('navigation', { name: 'My games' });
  expect(within(nav).getAllByRole('button')).toHaveLength(1);
  expect(nav.querySelector('[data-game-id="s1"]')).toHaveAttribute('aria-current', 'page');
});

it('loads every online page and includes waiting games while excluding completed games', async () => {
  const pages = Array.from({ length: 51 }, (_, i) =>
    onlineGame(`o${i}`, i === 50 ? 'waiting' : 'active'),
  );
  pages.push(onlineGame('completed', 'completed'));
  const paged = vi.fn((path: string) => {
    if (path === '/api/games')
      return respond([singleGame('s1'), singleGame('finished', { recorded_winner: 'draw' })]);
    const offset = Number(new URL(path, 'http://test').searchParams.get('offset'));
    return respond({ games: pages.slice(offset, offset + 50), limit: 50, offset, section: 'mine' });
  });
  vi.stubGlobal('fetch', paged);
  const open = vi.fn();
  renderList(open);
  const last = await screen.findByRole('button', { name: /Online game.*Waiting/ });
  expect(
    screen.getByRole('navigation', { name: 'My games' }).querySelectorAll('button'),
  ).toHaveLength(52);
  expect(paged).toHaveBeenCalledWith(expect.stringContaining('offset=50'), expect.anything());
  fireEvent.click(last);
  expect(open).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'o50', type: 'online', waiting: true }),
  );
});

it('retains a readable partial list and retries a failed source', async () => {
  let fail = true;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/games') return respond([singleGame('s1')]);
      return fail
        ? Promise.reject(new Error('offline'))
        : respond({ games: [onlineGame('o1', 'active')], limit: 50, offset: 0, section: 'mine' });
    }),
  );
  renderList();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Some running games could not be loaded.',
  );
  expect(
    screen.getByRole('navigation', { name: 'My games' }).querySelector('[data-game-id="s1"]'),
  ).toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(
    screen.getByRole('navigation', { name: 'My games' }).querySelectorAll('button'),
  ).toHaveLength(2);
});
