import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  vi.stubGlobal('fetch', fetcher);
  fetcher.mockClear();
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('renders nothing with zero or one active game', async () => {
  singles = [singleGame('s1')];
  onlines = [];
  const { container } = render(<ActiveGameSwitcher accountId="me" currentId="s1" open={vi.fn()} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  await waitFor(() => expect(container.querySelector('.active-game-switcher')).toBeNull());
});

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
