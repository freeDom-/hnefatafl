import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ActiveGameSwitcher from './ActiveGameSwitcher';
import { setLanguage } from './i18n';

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
